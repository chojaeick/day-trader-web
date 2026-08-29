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
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

FEE_RT_PCT = 0.25


def net_stats(label, trades):
    gross = pd.to_numeric(trades.pnl_pct, errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    net = gross - FEE_RT_PCT
    n = len(net)
    gp = float(net[net > 0].sum()) if n else 0.0
    gl = float(-net[net < 0].sum()) if n else 0.0
    return {
        'label': label,
        'trades': n,
        'gross_wins': int((gross > 0).sum()),
        'gross_win_pct': float((gross > 0).mean() * 100.0) if n else 0.0,
        'gross_sum_pct': float(gross.sum()) if n else 0.0,
        'net_wins': int((net > 0).sum()),
        'net_losses': int((net <= 0).sum()),
        'net_win_pct': float((net > 0).mean() * 100.0) if n else 0.0,
        'net_sum_pct': float(net.sum()) if n else 0.0,
        'net_avg_pct': float(net.mean()) if n else 0.0,
        'net_pf': gp / gl if gl > 0 else np.inf,
        'net_maxloss_pct': float(net.min()) if n else np.nan,
    }


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
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, _, _ = v17b.build_v17b(ev16, scored, waits)

    micros = {str(sym).zfill(6): h.build_micro(bars, cfg) for sym, bars in raw.items()}
    ev_v18, _ = h.build_veto_stream(ev_v17c, micros)

    t_v17c = multi.simulate_multi(packed, ev_v17c, states, v19.THRESHOLD)
    t_v18 = multi.simulate_multi(packed, ev_v18, states, v19.THRESHOLD)

    rows = [net_stats('V17C', t_v17c), net_stats('V18', t_v18)]
    cases = {}
    for d in v19.MAX_DELAYS:
        fast, diag = v19.build_v19_events(scored, micros, raw, d)
        merged, raw_added = v19.merge_additive(ev_v18, fast)
        t = multi.simulate_multi(packed, merged, states, v19.THRESHOLD)
        label = f'V19_STRICT_PREBUY_DELAY_LE_{d}M'
        rows.append(net_stats(label, t))
        cases[d] = (t, diag, raw_added)

    summary = pd.DataFrame(rows)
    print('=== V19 NET 0.25% ROUND-TRIP VALIDATION ===')
    print('Each trade is reclassified AFTER subtracting 0.25 percentage points.')
    print('Primary selection = net_sum_pct, then net_pf, then net_win_pct.')
    print('\n=== NET SUMMARY ===')
    print(summary.to_string(index=False))

    best = summary.iloc[2:].sort_values(
        ['net_sum_pct', 'net_pf', 'net_win_pct'], ascending=False
    ).iloc[0]
    best_delay = int(str(best['label']).split('_LE_')[1].replace('M', ''))
    print('\n=== BEST V19 BY NET 0.25% ===')
    print(best.to_string())

    best_t = cases[best_delay][0].copy()
    best_t['gross_pnl_pct'] = pd.to_numeric(best_t['pnl_pct'], errors='coerce')
    best_t['net_pnl_pct'] = best_t['gross_pnl_pct'] - FEE_RT_PCT
    best_t['net_result'] = np.where(best_t['net_pnl_pct'] > 0, 'WIN', 'LOSS')

    flipped = best_t[(best_t['gross_pnl_pct'] > 0) & (best_t['net_pnl_pct'] <= 0)].copy()
    cols = ['symbol','entry_time','exit_time','gross_pnl_pct','net_pnl_pct','reason']
    print('\n=== GROSS WIN -> NET LOSS AFTER 0.25% ===')
    print(flipped[cols].sort_values('gross_pnl_pct').to_string(index=False) if len(flipped) else 'NONE')

    out = v19.OUT_DIR / 'v19_net025_summary.csv'
    detail = v19.OUT_DIR / f'v19_net025_best_delay_{best_delay}m_trades.csv'
    summary.to_csv(out, index=False)
    best_t.to_csv(detail, index=False)
    print('\n[CSV]', out)
    print('[CSV]', detail)


if __name__ == '__main__':
    main()
