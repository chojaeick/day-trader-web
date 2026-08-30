from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
import tools.validate_engine5_v21_v_rebound_reaccel as ra
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.5
GAP_KEEP_MINS = [0.5, 0.7, 0.8, 0.9]


def n(x):
    return str(x).zfill(6)


def f(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan


def add_preservation(cand, feature_by_symbol):
    out = cand.copy()
    cols = [
        'pullback_minutes','high_gap_delta','min_gap_delta','gap_keep_ratio','gap_positive_all',
        'high_rsi_slope','min_rsi_slope','rsi_keep_ratio','rsi_positive_all'
    ]
    for c in cols:
        out[c] = np.nan
    out['gap_positive_all'] = False
    out['rsi_positive_all'] = False

    for idx, r in out.iterrows():
        sym = n(r.symbol)
        z = feature_by_symbol.get(sym)
        if z is None or len(z) == 0:
            continue
        zz = z.copy()
        zz['time'] = pd.to_datetime(zz.time)
        t0 = pd.Timestamp(r.first_rebound_high_time)
        t1 = pd.Timestamp(r.time)
        w = zz[(zz.time >= t0) & (zz.time <= t1)]
        if w.empty:
            continue
        gd = pd.to_numeric(w.gap_delta, errors='coerce').dropna()
        rs = pd.to_numeric(w.rsi_slope, errors='coerce').dropna()
        if len(gd):
            high_gap = f(gd.iloc[0]); min_gap = f(gd.min())
            out.at[idx, 'high_gap_delta'] = high_gap
            out.at[idx, 'min_gap_delta'] = min_gap
            out.at[idx, 'gap_keep_ratio'] = min_gap/high_gap if np.isfinite(high_gap) and high_gap != 0 else np.nan
            out.at[idx, 'gap_positive_all'] = bool((gd > 0).all())
        if len(rs):
            high_rsi = f(rs.iloc[0]); min_rsi = f(rs.min())
            out.at[idx, 'high_rsi_slope'] = high_rsi
            out.at[idx, 'min_rsi_slope'] = min_rsi
            out.at[idx, 'rsi_keep_ratio'] = min_rsi/high_rsi if np.isfinite(high_rsi) and high_rsi != 0 else np.nan
            out.at[idx, 'rsi_positive_all'] = bool((rs > 0).all())
        out.at[idx, 'pullback_minutes'] = (t1-t0).total_seconds()/60.0
    return out


def main():
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2., rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(x) for s, x in frames.items()}
    scored = {n(s): x for s, x in reweight(f10, cfg, 0.).items()}
    strength = {s: ms.add_strength(x) for s, x in scored.items()}
    completed = {s: rt.add_completed_strength(x) for s, x in scored.items()}
    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)

    micros = {}; allc = []; feature_by_symbol = {}
    for k, (sym, bars) in enumerate(raw.items(), 1):
        print(f'[{k}/{len(raw)}] {sym}', flush=True)
        pf, m = old.load_cache(sym, bars, cfg, completed[sym])
        micros[sym] = m
        z = sm.add_features(pf, m, bars).sort_values('time').reset_index(drop=True)
        feature_by_symbol[sym] = z
        c = sm.state_candidates(sym, z, scored[sym], RAW_MIN, LEG_MIN)
        if len(c):
            allc.append(c)

    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength, raw_min=52., rel_min=1.45)
    base_tr = multi.simulate_multi(packed, ev20, states, THRESHOLD)
    print('\n=== BASE V20 ===')
    print(pd.DataFrame([sm.stat('V20', base_tr)]).to_string(index=False))

    cand = pd.concat(allc, ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO V CANDIDATES')
        return
    cand = ra.add_pullback_reaccel(cand, feature_by_symbol)
    cand = add_preservation(cand, feature_by_symbol)

    eligible = cand[
        (cand.stop_dist_pct <= STOP_CAP) &
        (pd.to_numeric(cand.volume_accel, errors='coerce') >= VOL_MIN) &
        cand.reaccel_pass
    ].copy()
    eligible['day'] = pd.to_datetime(eligible.time).dt.date
    eligible = eligible.sort_values('time').drop_duplicates(['symbol','day'], keep='first').reset_index(drop=True)

    print('\n=== PULLBACK MOMENTUM PRESERVATION SWEEP ===')
    print('Fixed: RAW30 LEG2.0 STOP<=2.0 VOL>=1.5 REACCEL=ON')
    print('Sweep only: RSI slope positive throughout pullback, and MACD gap_delta minimum/high ratio.')

    rows = []
    configs = [('BASE_REACCEL', False, None)]
    for rsi_pos in [False, True]:
        for keep in GAP_KEEP_MINS:
            configs.append((f'RSIPOS_{int(rsi_pos)}_GAPKEEP_{keep:.1f}', rsi_pos, keep))

    for label, require_rsi_pos, keep in configs:
        q = eligible.copy()
        if keep is not None:
            q = q[pd.to_numeric(q.gap_keep_ratio, errors='coerce') >= keep]
            if require_rsi_pos:
                q = q[q.rsi_positive_all]
        vev, meta, qsel = sm.select(q, RAW_MIN, LEG_MIN, STOP_CAP, VOL_MIN)
        extra = old.simulate_with_v_stop(packed, vev, states, THRESHOLD, meta)
        merged = old.simulate_with_v_stop(packed, sm.merge(ev20, vev), states, THRESHOLD, meta)
        se = sm.stat('EXTRA', extra); sx = sm.stat('MERGED', merged)
        rows.append(dict(config=label, signals=len(qsel), **sx,
                         extra_trades=se['trades'], extra_wins=se['wins'], extra_win_pct=se['win_pct'],
                         extra_net=se['net_sum_pct'], extra_pf=se['pf'], extra_max_loss=se['max_loss_pct']))

    summary = pd.DataFrame(rows).sort_values(['extra_net','extra_pf','net_sum_pct'], ascending=False)
    print(summary.to_string(index=False))
    summary.to_csv(sm.OUT_DIR/'v21_v_rebound_momentum_preservation_summary.csv', index=False)

    show_cols = ['symbol','time','price','structural_stop','stop_dist_pct','volume_accel','pullback_minutes',
                 'high_gap_delta','min_gap_delta','gap_keep_ratio','gap_positive_all',
                 'high_rsi_slope','min_rsi_slope','rsi_keep_ratio','rsi_positive_all']
    print('\n=== ELIGIBLE REACCEL SIGNALS + PRESERVATION METRICS ===')
    print(eligible[show_cols].to_string(index=False) if len(eligible) else 'NONE')
    eligible.drop(columns=['event'], errors='ignore').to_csv(sm.OUT_DIR/'v21_v_rebound_momentum_preservation_candidates.csv', index=False)

    for keep in GAP_KEEP_MINS:
        q = eligible[(pd.to_numeric(eligible.gap_keep_ratio, errors='coerce') >= keep) & eligible.rsi_positive_all]
        print(f'\n=== RSI POSITIVE ALL + GAP KEEP >= {keep:.1f} ===')
        print(q[show_cols].to_string(index=False) if len(q) else 'NONE')

    print('\nWROTE v21_v_rebound_momentum_preservation_candidates.csv / summary.csv')

if __name__ == '__main__':
    main()
