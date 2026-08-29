from __future__ import annotations

from dataclasses import replace

import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_engine5_fast_tuner_v4 import build_cfg_frames, pack_state_events
from tools.backtest_dbb_engine5_fast_tuner_v6 import pack_entry_events, pack_exit_events, simulate_v6
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

TARGET_SYMBOL = '233740'
TARGET_DATE = '2026-08-24'
THRESHOLD = 60.0


def main():
    raw_all = load_data()
    raw = {TARGET_SYMBOL: raw_all[TARGET_SYMBOL]}
    cfg = replace(DoubleBollingerEngine5Config(), w_rsi_accel=15.0, w_volume=0.0, w_outer_expand=0.0)
    frames = build_cfg_frames(raw, cfg)
    scored = reweight(frames, cfg, 0.0)
    entry_events = pack_entry_events(scored)
    state_events = pack_state_events(frames)
    packed_exits = pack_exit_events(raw, cfg)

    f = scored[TARGET_SYMBOL].copy()
    f['time'] = pd.to_datetime(f['time'])
    day = pd.Timestamp(TARGET_DATE).date()
    q = f[(f['time'].dt.date == day) & (f['entry_gate'])].copy()
    print('=== ENGINE 5 V6 SYMBOL-DAY REPLAY ===')
    print(f'symbol={TARGET_SYMBOL} date={TARGET_DATE} threshold={THRESHOLD}')
    print('\n--- 5M ENTRY CANDIDATES ---')
    cols = ['time','close','entry_score','entry_gate','mid_slope8','macd_slope_spread','rsi_slope','outer_expanding','inner_upper','inner_lower','outer_upper']
    print(q[cols].to_string(index=False))

    trades, collisions = simulate_v6(packed_exits, entry_events, state_events, THRESHOLD)
    t = trades.copy()
    if len(t):
        t['entry_time'] = pd.to_datetime(t['entry_time'])
        t = t[t['entry_time'].dt.date == day]
    print('\n--- V6 SIMULATED TRADES ---')
    if t.empty:
        print('NONE')
    else:
        outcols = ['entry_time','entry_price','r_abs','stop_dist','stop_price','tp1_price','extended_entry','first_tp_done','second_tp_done','exit_time','exit_price','pnl_pct','reason']
        print(t[outcols].to_string(index=False))
    print(f'collisions={collisions}')
    print('\nEXPECTED CHECKS:')
    print('1) 09:05 TP1 must use raw inner-band R (~41.12), so TP1 should be near 6942.24.')
    print('2) Ordinary pre-TP1 momentum fade must not close the 09:05 trade before TP1.')
    print('3) After an exit, a fresh >=60 entry around 11:25 may re-enter with no cooldown.')
    print('4) After TP1, a sustained two-bar 1m MACD-spread + RSI-slope fade should protect the remainder near the turn instead of waiting to 12:30.')


if __name__ == '__main__':
    main()
