from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
import tools.diagnose_v20_transition_structure_targets as st
import tools.validate_engine5_slow_turn_prototype as slow
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
SLOPE_LBS = [3, 5]
SLOPE_POS_RATIOS = [0.67, 0.80]
MICRO_POS_RATIOS = [0.67, 0.80]
READY_MAX_MIN = 5


def n(x):
    return str(x).zfill(6)


def finite(x):
    return slow.finite(x)


def event_from_completed(sym: str, completed_row, mr):
    iu = finite(completed_row.get('inner_upper'))
    il = finite(completed_row.get('inner_lower'))
    ou = finite(completed_row.get('outer_upper'))
    mid = finite(completed_row.get('mid'))
    px = finite(mr.get('close'))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(px) or not np.isfinite(band_r) or band_r <= 0:
        return None
    score = max(float(THRESHOLD), finite(completed_row.get('entry_score', THRESHOLD)))
    stop_ref = finite(mr.get('prior_low_3'))
    stop_dist = px - stop_ref if np.isfinite(stop_ref) and stop_ref < px else band_r
    return (
        n(sym), px, score,
        finite(completed_row.get('macd_slope_spread_strength', np.nan)),
        finite(completed_row.get('rsi_slope_strength', np.nan)),
        float(band_r), float(stop_dist), iu, il, ou, mid,
        bool(np.isfinite(ou) and px > ou), False,
    )


def build_candidates(sym, pf, micro, scored5, slope_lb, slope_ratio, micro_ratio):
    z, m = slow.add_slow_turn_features(pf, micro)
    mid = pd.to_numeric(z['mid_slope8'], errors='coerce')
    gd = pd.to_numeric(z.get('gap_delta'), errors='coerce')
    rs = pd.to_numeric(z.get('rsi_slope'), errors='coerce')

    ready = (
        (mid < 0)
        & (pd.to_numeric(z[f'slope_gain_{slope_lb}'], errors='coerce') > 0)
        & (pd.to_numeric(z[f'slope_pos_ratio_{slope_lb}'], errors='coerce') >= slope_ratio)
        & (gd > 0)
        & (rs > 0)
    )

    rows = []
    seen_day = set()
    sf = scored5.copy().sort_values('time')
    sf['time'] = pd.to_datetime(sf['time'])

    for _, r in z[ready].iterrows():
        ts = pd.Timestamp(r.time)
        minute = ts.hour * 60 + ts.minute
        if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
            continue
        key = (n(sym), ts.date())
        if key in seen_day:
            continue

        mr = slow.first_micro_confirmation(m, ts, micro_ratio)
        if mr is None:
            continue

        # IMPORTANT FIX: provisional rows intentionally contain transition indicators only.
        # Use the most recent completed 5m row for DBB geometry/event tuple construction.
        exec_ts = pd.Timestamp(mr.time)
        q5 = sf[sf.time <= exec_ts.floor('5min')]
        if q5.empty:
            continue
        completed_row = q5.iloc[-1]
        ev = event_from_completed(sym, completed_row, mr)
        if ev is None:
            continue

        seen_day.add(key)
        rows.append(dict(
            symbol=n(sym), ready_time=ts, entry_time=exec_ts,
            entry_price=finite(mr.close), mid_slope8=finite(r.mid_slope8),
            slope_gain=finite(r[f'slope_gain_{slope_lb}']),
            slope_pos_ratio=finite(r[f'slope_pos_ratio_{slope_lb}']),
            gap_delta_5m=finite(r.get('gap_delta')), rsi_slope_5m=finite(r.get('rsi_slope')),
            gap_pos_ratio_1m=finite(mr.gap_pos_ratio_3), rsi_pos_ratio_1m=finite(mr.rsi_pos_ratio_3),
            prior_low_3=finite(mr.prior_low_3), prior_high_3=finite(mr.prior_high_3),
            band_r=float(ev[5]), stop_dist=float(ev[6]),
            event=ev, source='SLOW_TURN',
        ))
    return pd.DataFrame(rows)


def stat(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    net = p - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(label=label, trades=len(net), wins=int((net > 0).sum()),
                win_pct=float((net > 0).mean() * 100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                pf=(gp / gl if gl > 0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength, raw_min=52.0, rel_min=1.45)
    base_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)

    print('=== SLOW TURN PROTOTYPE FIX / V20 IS FROZEN ===')
    print(pd.DataFrame([stat('V20_FROZEN', base_trades)]).to_string(index=False))
    print('FIX: transition conditions unchanged; only DBB event geometry now comes from the latest completed 5m row.')

    pf_by_symbol = {}
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        pf_by_symbol[s] = pf

    rows = []
    selected_all = []
    v20_keys = {(pd.Timestamp(ts), n(c[0])) for ts, cs in ev20.items() for c in cs}

    for slb in SLOPE_LBS:
        for sr in SLOPE_POS_RATIOS:
            for mr in MICRO_POS_RATIOS:
                parts = []
                for s in raw:
                    q = build_candidates(s, pf_by_symbol[s], micros[s], scored[s], slb, sr, mr)
                    if len(q):
                        parts.append(q)
                cand = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                sev = slow.event_stream(cand)
                extra = multi.simulate_multi(packed, sev, states, THRESHOLD)
                merged = multi.simulate_multi(packed, slow.merge_streams(ev20, sev), states, THRESHOLD)
                se = stat('EXTRA', extra)
                smg = stat('MERGED', merged)
                overlap = 0
                if len(cand):
                    overlap = sum((pd.Timestamp(r.entry_time), n(r.symbol)) in v20_keys for _, r in cand.iterrows())
                    x = cand.copy()
                    x['slope_lb'] = slb
                    x['slope_ratio_cfg'] = sr
                    x['micro_ratio_cfg'] = mr
                    selected_all.append(x)
                rows.append(dict(
                    slope_lb=slb, slope_pos_ratio=sr, micro_pos_ratio=mr,
                    signals=len(cand), overlaps_v20=overlap,
                    extra_trades=se['trades'], extra_wins=se['wins'], extra_win_pct=se['win_pct'],
                    extra_net=se['net_sum_pct'], extra_pf=se['pf'], extra_max_loss=se['max_loss_pct'],
                    merged_trades=smg['trades'], merged_wins=smg['wins'], merged_win_pct=smg['win_pct'],
                    merged_net=smg['net_sum_pct'], merged_pf=smg['pf'], merged_max_loss=smg['max_loss_pct'],
                ))

    summary = pd.DataFrame(rows).sort_values(['extra_net','extra_pf'], ascending=False)
    print('\n=== FIXED SWEEP SUMMARY ===')
    print(summary.to_string(index=False))

    if selected_all:
        sel = pd.concat(selected_all, ignore_index=True)
        print('\n=== FIXED CANDIDATES ===')
        cols = ['slope_lb','slope_ratio_cfg','micro_ratio_cfg','symbol','ready_time','entry_time','entry_price',
                'mid_slope8','slope_gain','slope_pos_ratio','gap_delta_5m','rsi_slope_5m',
                'gap_pos_ratio_1m','rsi_pos_ratio_1m','prior_low_3','prior_high_3','band_r','stop_dist']
        print(sel[cols].sort_values(['entry_time','symbol']).to_string(index=False))
        sel.drop(columns=['event'], errors='ignore').to_csv(OUT_DIR/'slow_turn_prototype_fix_candidates.csv', index=False)

    summary.to_csv(OUT_DIR/'slow_turn_prototype_fix_summary.csv', index=False)
    print('\nREGRESSION BASELINE MUST REMAIN: V20 39 trades / 17 wins / +20.012131% net / PF 1.86292.')
    print('WROTE', OUT_DIR/'slow_turn_prototype_fix_summary.csv')
    print('WROTE', OUT_DIR/'slow_turn_prototype_fix_candidates.csv')


if __name__ == '__main__':
    main()
