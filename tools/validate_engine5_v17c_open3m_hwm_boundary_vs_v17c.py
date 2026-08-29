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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
PROTECT_MINUTES = 3
DD_LEVELS = (0.0225, 0.0250, 0.0275, 0.0300, 0.0325, 0.0350)
OUT_DIR = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'


def stats(label, t, opening_hwm_exits=0):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    wins = int((p > 0).sum())
    losses = int((p <= 0).sum())
    gross = float(p.sum()) if len(p) else 0.0
    avg = float(p.mean()) if len(p) else 0.0
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    pf = gp / gl if gl > 0 else np.inf
    maxloss = float(p.min()) if len(p) else np.nan
    return {
        'label': label,
        'trades': len(p),
        'wins': wins,
        'losses': losses,
        'win_pct': wins / len(p) * 100.0 if len(p) else 0.0,
        'gross_pct': gross,
        'avg_pct': avg,
        'pf': pf,
        'maxloss_pct': maxloss,
        'opening_hwm_exits': int(opening_hwm_exits),
        'structural_exits': int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()) if len(t) else 0,
    }


def print_case(label, t):
    print(f'\n=== {label} ===')
    multi.metrics(label, t)
    hwm_mask = t.reason.astype(str).str.startswith('OPENING_5M_HWM_') if len(t) else pd.Series(dtype=bool)
    print('OPENING_HWM_EXITS=', int(hwm_mask.sum()) if len(t) else 0)
    print('STRUCTURAL_EXITS=', int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()) if len(t) else 0)
    q = t[(t.symbol.astype(str).str.zfill(6) == '058610') &
          (pd.to_datetime(t.entry_time) == pd.Timestamp('2026-08-11 09:10:00+09:00'))] if len(t) else t
    print('TARGET_058610_2026-08-11_0910:')
    print(q.to_string(index=False) if len(q) else 'NOT_PRESENT')
    return int(hwm_mask.sum()) if len(t) else 0


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)

    # ORIGINAL V17C: inherited V16 WAIT/re-acceleration + V17B breakout/veto.
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, added_v17c, skipped_v17c = v17b.build_v17b(ev16, scored, waits)
    original = multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)

    # CONTROL: intended 09:10+ immediate entries, no opening HWM; normal V17C exits remain.
    ev_control, added_control, skipped_control = v17b.build_v17b(ev10, scored, pd.DataFrame())
    control = multi.simulate_multi(packed, ev_control, states, THRESHOLD)

    print('=== V17C OPEN3M HWM BOUNDARY + DIRECT V17C COMPARISON ===')
    print('ORIGINAL_V17C: existing V16 WAIT/reaccel + V17B/V17C multi-symbol behavior.')
    print('NO_OPEN_HWM_CONTROL: 09:00-09:09 blocked only; 09:10+ immediate entries; no special opening HWM.')
    print('CANDIDATES: same immediate entries + first 3 minutes completed-HWM HWM_FIRST protection.')
    print('DD:', ', '.join(f'{x*100:.2f}%' for x in DD_LEVELS))
    print('Breakout first-10m completed-HWM -1% remains in all cases.')
    print('ORIGINAL_BREAKOUT_ADDED=', added_v17c)
    print('ORIGINAL_BREAKOUT_SKIPPED=', skipped_v17c)
    print('CONTROL_BREAKOUT_ADDED=', added_control)
    print('CONTROL_BREAKOUT_SKIPPED=', skipped_control)

    rows = []

    h = print_case('ORIGINAL_V17C', original)
    rows.append(stats('ORIGINAL_V17C', original, h))
    original.to_csv(f'{OUT_DIR}/v17c_original_direct_compare.csv', index=False)

    h = print_case('NO_OPEN_HWM_CONTROL', control)
    rows.append(stats('NO_OPEN_HWM_CONTROL', control, h))
    control.to_csv(f'{OUT_DIR}/v17c_no_open_hwm_control.csv', index=False)

    sweep.PROTECT_MINUTES = PROTECT_MINUTES
    for dd in DD_LEVELS:
        label = f'OPEN3M_{dd*100:.2f}PCT_HWM_FIRST'
        t = sweep.simulate_5m_hwm(packed, ev_control, states, THRESHOLD, dd, True)
        h = print_case(label, t)
        rows.append(stats(label, t, h))
        t.to_csv(f'{OUT_DIR}/v17c_open3m_{dd*100:.2f}pct_hwm_first_boundary.csv', index=False)

    summary = pd.DataFrame(rows)
    base_row = summary.loc[summary.label == 'ORIGINAL_V17C'].iloc[0]
    control_row = summary.loc[summary.label == 'NO_OPEN_HWM_CONTROL'].iloc[0]
    summary['gross_vs_v17c_pp'] = summary.gross_pct - float(base_row.gross_pct)
    summary['pf_vs_v17c'] = summary.pf - float(base_row.pf)
    summary['gross_vs_control_pp'] = summary.gross_pct - float(control_row.gross_pct)
    summary['pf_vs_control'] = summary.pf - float(control_row.pf)

    print('\n=== DIRECT COMPARISON SORTED BY GROSS ===')
    print(summary.sort_values(['gross_pct', 'pf'], ascending=False).to_string(index=False))

    candidates = summary[summary.label.str.startswith('OPEN3M_')].sort_values(['gross_pct', 'pf'], ascending=False)
    print('\n=== BEST HWM CANDIDATES ===')
    print(candidates.head(6).to_string(index=False))

    print('\n=== BASELINE DELTAS ===')
    print('ORIGINAL_V17C_GROSS=', f'{base_row.gross_pct:+.6f}%')
    print('ORIGINAL_V17C_PF=', f'{base_row.pf:.6f}')
    print('NO_OPEN_HWM_CONTROL_GROSS=', f'{control_row.gross_pct:+.6f}%')
    print('NO_OPEN_HWM_CONTROL_PF=', f'{control_row.pf:.6f}')
    if len(candidates):
        best = candidates.iloc[0]
        print('BEST=', best.label)
        print('BEST_GROSS=', f'{best.gross_pct:+.6f}%')
        print('BEST_PF=', f'{best.pf:.6f}')
        print('BEST_GROSS_VS_V17C=', f'{best.gross_vs_v17c_pp:+.6f}pp')
        print('BEST_GROSS_VS_CONTROL=', f'{best.gross_vs_control_pp:+.6f}pp')
        print('BEST_PF_VS_V17C=', f'{best.pf_vs_v17c:+.6f}')
        print('BEST_PF_VS_CONTROL=', f'{best.pf_vs_control:+.6f}')

    out = f'{OUT_DIR}/v17c_open3m_hwm_boundary_vs_v17c_summary.csv'
    summary.to_csv(out, index=False)
    print('[SUMMARY CSV]', out)


if __name__ == '__main__':
    main()
