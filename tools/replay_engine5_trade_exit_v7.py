from __future__ import annotations

from dataclasses import replace

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v6 as v6
import tools.backtest_dbb_engine5_fast_tuner_v7 as v7
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

TARGET_SYMBOL = '233740'
TARGET_DATE = '2026-08-24'
THRESHOLD = 60.0


def main():
    raw = load_data()
    cfg = replace(DoubleBollingerEngine5Config(), w_rsi_accel=15.0, w_volume=0.0, w_outer_expand=0.0)
    frames = base.build_cfg_frames(raw, cfg)
    scored = reweight(frames, cfg, 0.0)

    q = scored[TARGET_SYMBOL].copy()
    q['time'] = pd.to_datetime(q['time'])
    q = q[q['time'].dt.strftime('%Y-%m-%d') == TARGET_DATE]
    q = q[q['entry_gate']].copy()
    cols = ['time','close','entry_score','entry_gate','mid_slope8','macd_slope_spread','rsi_slope','outer_expanding','inner_upper','inner_lower','outer_upper']

    print('=== ENGINE 5 V7 SYMBOL-DAY REPLAY ===')
    print(f'symbol={TARGET_SYMBOL} date={TARGET_DATE} threshold={THRESHOLD}')
    print('\n--- 5M ENTRY CANDIDATES ---')
    print(q[cols].to_string(index=False))

    packed_exits = v6.pack_exit_events({TARGET_SYMBOL: raw[TARGET_SYMBOL]}, cfg)
    state_events = base.pack_state_events({TARGET_SYMBOL: frames[TARGET_SYMBOL]})
    entry_events = v6.pack_entry_events({TARGET_SYMBOL: scored[TARGET_SYMBOL]})
    trades, collisions = v7.simulate_v7(packed_exits, entry_events, state_events, THRESHOLD)

    print('\n--- V7 SIMULATED TRADES ---')
    show = ['entry_time','entry_price','r_abs','stop_dist','stop_price','tp1_price','extended_entry','first_tp_done','second_tp_done','exit_time','exit_price','pnl_pct','reason']
    print(trades[show].to_string(index=False) if len(trades) else 'NO TRADES')
    print(f'collisions={collisions}')

    print('\nEXPECTED CHECKS:')
    print('1) 09:05 TP1 should still be near 6942.24 using raw inner-band R.')
    print('2) Immediate post-TP1 noise should NOT exit around 09:08 unless continuation was first armed.')
    print('3) Fresh >=60 entry around 11:25 may re-enter with no cooldown.')
    print('4) The 11:25 wave should still exit near the 11:40 turn once continuation was armed and 1m MACD+RSI fade persisted two bars.')


if __name__ == '__main__':
    main()
