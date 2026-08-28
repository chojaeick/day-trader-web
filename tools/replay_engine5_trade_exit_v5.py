from __future__ import annotations

from dataclasses import replace

import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_engine5_fast_tuner_v4 import (
    build_cfg_frames,
    pack_entry_events,
    pack_exit_events,
    pack_state_events,
    simulate_v4,
)
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

SYMBOL = '233740'
DATE = '2026-08-24'
THRESHOLD = 60.0


def main():
    raw = load_data()
    cfg = replace(DoubleBollingerEngine5Config(), w_rsi_accel=15.0, w_volume=0.0, w_outer_expand=0.0)
    frames = build_cfg_frames(raw, cfg)
    scored = reweight(frames, cfg, 0.0)
    f = scored[SYMBOL].copy()
    f['time'] = pd.to_datetime(f['time'])

    print('=== ENGINE 5 V5 DAY REPLAY ===')
    print(f'symbol={SYMBOL} date={DATE} threshold={THRESHOLD}')
    print('\n--- 5M ENTRY CANDIDATES / MOMENTUM ---')
    day = f[f['time'].dt.strftime('%Y-%m-%d') == DATE].copy()
    cols = [
        'time','close','entry_score','entry_gate','mid_slope8','macd','macd_signal','macd_slope',
        'macd_signal_slope','macd_slope_spread','rsi','rsi_slope','outer_expanding',
        'inner_upper','inner_lower','outer_upper'
    ]
    q = day[(day['entry_gate']) | (day['time'].dt.strftime('%H:%M').isin(['09:05','11:20','11:25','11:30','11:35','11:40','11:45']))]
    print(q[[c for c in cols if c in q.columns]].to_string(index=False))

    packed_exits = pack_exit_events(raw, cfg)
    states = pack_state_events(frames)
    entries = pack_entry_events(scored)
    trades, collisions = simulate_v4(packed_exits, entries, states, THRESHOLD)
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    trades['exit_time'] = pd.to_datetime(trades['exit_time'])
    t = trades[(trades['symbol'].astype(str) == SYMBOL) & (trades['entry_time'].dt.strftime('%Y-%m-%d') == DATE)].copy()

    print('\n--- SIMULATED TRADES ---')
    if t.empty:
        print('no trades')
    else:
        show = ['entry_time','entry_price','r_abs','r_pct','stop_price','tp1_price','first_tp_done','second_tp_done','exit_time','exit_price','pnl_pct','reason']
        print(t[[c for c in show if c in t.columns]].to_string(index=False))
    print(f'collisions={collisions}')

    print('\nCHECKPOINTS:')
    print('1) 09:05 trade should use corrected structural R if entry is above inner/outer bands.')
    print('2) After an exit, a fresh 11:20-area entry_gate may create a new trade; there is no cooldown.')
    print('3) Around 11:40, if at least two of DBB-mid slope / MACD spread / RSI slope have turned non-positive, remaining shares should exit via trend-fade protection.')


if __name__ == '__main__':
    main()
