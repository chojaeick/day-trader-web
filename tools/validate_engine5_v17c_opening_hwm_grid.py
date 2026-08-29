from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
PROTECT_WINDOWS = (3, 5, 7)
DD_LEVELS = (0.015, 0.0175, 0.020, 0.0225, 0.025)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev, added, skipped = v17b.build_v17b(ev10, scored, pd.DataFrame())

    print('=== V17C OPENING HWM GRID ===')
    print('BUY: 09:00-09:09 blocked only; 09:10+ valid signals tradable immediately.')
    print('PRIORITY: HWM_FIRST fixed.')
    print('WINDOWS: 3m, 5m, 7m.')
    print('DD: 1.50%, 1.75%, 2.00%, 2.25%, 2.50%.')
    print('HWM: completed-HWM only. Structural stop remains after HWM priority check.')
    print('Breakout entries retain existing first-10m completed-HWM -1% rule.')
    print('BREAKOUT_ADDED=', added)
    print('BREAKOUT_SKIPPED=', skipped)

    out_dir = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'
    rows = []

    for minutes in PROTECT_WINDOWS:
        sweep.PROTECT_MINUTES = minutes
        for dd in DD_LEVELS:
            label = f'OPEN{minutes}M_{dd*100:.2f}PCT_HWM_FIRST'
            t = sweep.simulate_5m_hwm(packed, ev, states, THRESHOLD, dd, True)

            print(f'\n=== {label} ===')
            multi.metrics(label, t)
            hwm_mask = t.reason.astype(str).str.startswith('OPENING_5M_HWM_')
            print('OPENING_HWM_EXITS=', int(hwm_mask.sum()))
            print('STRUCTURAL_EXITS=', int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()))
            q = t[(t.symbol.astype(str).str.zfill(6) == '058610') & (pd.to_datetime(t.entry_time) == pd.Timestamp('2026-08-11 09:10:00+09:00'))]
            print('TARGET_058610_2026-08-11_0910:')
            print(q.to_string(index=False) if len(q) else 'NOT_PRESENT')

            wins = int((t.pnl_pct > 0).sum())
            losses = int((t.pnl_pct <= 0).sum())
            gross = float(t.pnl_pct.sum()) if len(t) else 0.0
            avg = float(t.pnl_pct.mean()) if len(t) else 0.0
            pos_sum = float(t.loc[t.pnl_pct > 0, 'pnl_pct'].sum()) if len(t) else 0.0
            neg_sum = float(-t.loc[t.pnl_pct < 0, 'pnl_pct'].sum()) if len(t) else 0.0
            pf = pos_sum / neg_sum if neg_sum > 0 else np.inf
            maxloss = float(t.pnl_pct.min()) if len(t) else 0.0

            out = f'{out_dir}/v17c_open{minutes}m_{dd*100:.2f}pct_hwm_first.csv'
            t.to_csv(out, index=False)
            print('[CSV]', out)

            rows.append({
                'protect_minutes': minutes,
                'dd_pct': dd * 100.0,
                'label': label,
                'trades': len(t),
                'wins': wins,
                'losses': losses,
                'win_pct': wins / len(t) * 100.0 if len(t) else 0.0,
                'gross_pct': gross,
                'avg_pct': avg,
                'pf': pf,
                'maxloss_pct': maxloss,
                'opening_hwm_exits': int(hwm_mask.sum()),
                'structural_exits': int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()),
            })

    summary = pd.DataFrame(rows).sort_values(['gross_pct', 'pf', 'avg_pct'], ascending=False)
    print('\n=== GRID COMPARISON SORTED BY GROSS ===')
    print(summary.to_string(index=False))

    print('\n=== BEST BY WINDOW ===')
    best_window = summary.sort_values(['protect_minutes', 'gross_pct', 'pf'], ascending=[True, False, False]).groupby('protect_minutes', as_index=False).first()
    print(best_window.to_string(index=False))

    print('\n=== BEST OVERALL ===')
    print(summary.head(5).to_string(index=False))

    summary_out = f'{out_dir}/v17c_opening_hwm_grid_summary.csv'
    summary.to_csv(summary_out, index=False)
    print('[SUMMARY CSV]', summary_out)


if __name__ == '__main__':
    main()
