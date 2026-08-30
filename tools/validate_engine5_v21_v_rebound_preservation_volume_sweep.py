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
import tools.validate_engine5_v21_v_rebound_momentum_preservation as mp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MINS = [None, 1.0, 1.2, 1.5]
GAP_KEEP_MINS = [0.7, 0.8, 0.9]


def n(x):
    return str(x).zfill(6)


def vol_label(v):
    return 'NONE' if v is None else f'{v:.1f}'


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

    micros = {}
    allc = []
    feature_by_symbol = {}
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
    cand = mp.add_preservation(cand, feature_by_symbol)
    cand = cand[(cand.stop_dist_pct <= STOP_CAP) & cand.reaccel_pass].copy()
    if cand.empty:
        print('NO STOP<=2.0 / REACCEL CANDIDATES')
        return

    print('\n=== PRESERVATION x VOLUME SWEEP ===')
    print('Fixed: RAW30 LEG2.0 STOP<=2.0 REACCEL=ON RSI slope positive throughout pullback.')
    print('Sweep only: VOL none/1.0/1.2/1.5 and MACD gap keep 0.7/0.8/0.9.')
    print('Candidate de-duplication is performed AFTER each volume filter, matching the existing selection path.')

    rows = []
    selected_rows = []
    for vol in VOL_MINS:
        e = cand.copy()
        if vol is not None:
            e = e[pd.to_numeric(e.volume_accel, errors='coerce') >= vol]
        e['day'] = pd.to_datetime(e.time).dt.date
        e = e.sort_values('time').drop_duplicates(['symbol', 'day'], keep='first').reset_index(drop=True)

        for keep in GAP_KEEP_MINS:
            q = e[
                e.rsi_positive_all &
                (pd.to_numeric(e.gap_keep_ratio, errors='coerce') >= keep)
            ].copy()
            vev, meta, qsel = sm.select(q, RAW_MIN, LEG_MIN, STOP_CAP, None)
            extra = old.simulate_with_v_stop(packed, vev, states, THRESHOLD, meta)
            merged = old.simulate_with_v_stop(packed, sm.merge(ev20, vev), states, THRESHOLD, meta)
            se = sm.stat('EXTRA', extra)
            sx = sm.stat('MERGED', merged)
            rows.append(dict(
                volume_min=vol_label(vol), gap_keep_min=keep, signals=len(qsel), **sx,
                extra_trades=se['trades'], extra_wins=se['wins'], extra_win_pct=se['win_pct'],
                extra_net=se['net_sum_pct'], extra_pf=se['pf'], extra_max_loss=se['max_loss_pct']
            ))
            if len(qsel):
                qq = qsel.copy()
                qq['volume_min_config'] = vol_label(vol)
                qq['gap_keep_min_config'] = keep
                selected_rows.append(qq)

    summary = pd.DataFrame(rows).sort_values(
        ['extra_net', 'extra_pf', 'net_sum_pct'], ascending=False
    )
    print(summary.to_string(index=False))
    summary.to_csv(sm.OUT_DIR/'v21_v_rebound_preservation_volume_sweep_summary.csv', index=False)

    show = ['symbol','time','price','structural_stop','stop_dist_pct','volume_accel','pullback_minutes',
            'gap_keep_ratio','rsi_keep_ratio','rsi_positive_all']

    print('\n=== CANDIDATE COUNTS BEFORE PRESERVATION ===')
    for vol in VOL_MINS:
        e = cand.copy()
        if vol is not None:
            e = e[pd.to_numeric(e.volume_accel, errors='coerce') >= vol]
        e['day'] = pd.to_datetime(e.time).dt.date
        e = e.sort_values('time').drop_duplicates(['symbol','day'], keep='first')
        print(f'VOL>={vol_label(vol)}: {len(e)} candidates')
        print(e[show].to_string(index=False) if len(e) else 'NONE')

    print('\n=== SELECTED SIGNALS BY CONFIG ===')
    if selected_rows:
        selected = pd.concat(selected_rows, ignore_index=True)
        cols = ['volume_min_config','gap_keep_min_config'] + show
        print(selected[cols].sort_values(['volume_min_config','gap_keep_min_config','time','symbol']).to_string(index=False))
        selected.drop(columns=['event'], errors='ignore').to_csv(
            sm.OUT_DIR/'v21_v_rebound_preservation_volume_sweep_selected.csv', index=False
        )
    else:
        print('NONE')

    cand.drop(columns=['event'], errors='ignore').to_csv(
        sm.OUT_DIR/'v21_v_rebound_preservation_volume_sweep_candidates.csv', index=False
    )
    print('\nWROTE preservation volume sweep candidates / selected / summary CSVs')


if __name__ == '__main__':
    main()
