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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_MIN = 52.0
REL_MIN = 1.45


def _norm_sym(x):
    return str(x).zfill(6)


def _strength_at(frames, sym, ts):
    f = frames.get(_norm_sym(sym))
    if f is None or f.empty:
        return np.nan, np.nan, np.nan
    q = f[f.time <= pd.Timestamp(ts)]
    if q.empty:
        return np.nan, np.nan, np.nan
    r = q.iloc[-1]
    return (
        h.finite(r.get('macd_strength_raw', np.nan)),
        h.finite(r.get('macd_strength_rel', np.nan)),
        h.finite(r.get('macd_strength_baseline', np.nan)),
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = reweight(f10, cfg, 0.0)
    strength_frames = {_norm_sym(s): ms.add_strength(f) for s, f in scored.items()}

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {_norm_sym(s): h.build_micro(b, cfg) for s, b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)

    ev20, diag = ms.filter_events(ev18, strength_frames, raw_min=RAW_MIN, rel_min=REL_MIN)
    trades = multi.simulate_multi(packed, ev20, states, THRESHOLD).copy()

    if trades.empty:
        print('NO V20 TRADES')
        return

    trades['symbol'] = trades['symbol'].map(_norm_sym)
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    trades['gross_pct'] = pd.to_numeric(trades['pnl_pct'], errors='coerce')
    trades['net_pct'] = trades['gross_pct'] - FEE_RT_PCT
    trades['result'] = np.where(trades['net_pct'] > 0, 'WIN', 'LOSS')

    raws, rels, bases = [], [], []
    for _, r in trades.iterrows():
        raw_v, rel_v, base_v = _strength_at(strength_frames, r.symbol, r.entry_time)
        raws.append(raw_v); rels.append(rel_v); bases.append(base_v)
    trades['macd_raw'] = raws
    trades['macd_rel'] = rels
    trades['macd_baseline'] = bases

    preferred = [
        'result','symbol','entry_time','exit_time','entry_price','exit_price',
        'gross_pct','net_pct','reason','macd_raw','macd_rel','macd_baseline'
    ]
    cols = [c for c in preferred if c in trades.columns]

    wins = trades[trades.net_pct > 0].sort_values('net_pct', ascending=False)
    losses = trades[trades.net_pct <= 0].sort_values('net_pct')

    print('=== V20 CURRENT CASES: RAW>=52 / REL>=1.45x ===')
    print(f'TRADES={len(trades)} WINS={len(wins)} LOSSES={len(losses)} WIN_RATE={len(wins)/len(trades)*100:.6f}%')
    print(f'NET_SUM={trades.net_pct.sum():+.6f}% AVG={trades.net_pct.mean():+.6f}%')

    print('\n=== SUCCESS CASES / CURRENT V20 ONLY ===')
    print(wins[cols].to_string(index=False))

    print('\n=== FAILURE CASES / CURRENT V20 ONLY ===')
    print(losses[cols].to_string(index=False))

    print('\n=== LOSS REASON COUNTS ===')
    if 'reason' in losses.columns:
        print(losses.groupby('reason').agg(n=('net_pct','size'), net_sum=('net_pct','sum'), avg=('net_pct','mean')).sort_values('net_sum').to_string())
    else:
        print('NO reason COLUMN')

    print('\n=== SANITY CHECK 950260 2026-08-21 10:00 ===')
    q = trades[(trades.symbol == '950260') & (trades.entry_time == pd.Timestamp('2026-08-21 10:00:00+09:00'))]
    print('ABSENT_FROM_CURRENT_V20' if q.empty else q[cols].to_string(index=False))

    all_path = OUT_DIR / 'v20_current_39_cases.csv'
    win_path = OUT_DIR / 'v20_current_17_wins.csv'
    loss_path = OUT_DIR / 'v20_current_22_losses.csv'
    trades[cols].sort_values('entry_time').to_csv(all_path, index=False)
    wins[cols].to_csv(win_path, index=False)
    losses[cols].to_csv(loss_path, index=False)

    print('\nWROTE', all_path)
    print('WROTE', win_path)
    print('WROTE', loss_path)


if __name__ == '__main__':
    main()
