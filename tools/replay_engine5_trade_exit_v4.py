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

TARGET_SYMBOL = '233740'
TARGET_TIME = pd.Timestamp('2026-08-24 09:05:00+09:00')
THRESHOLD = 60.0


def main():
    raw = load_data()
    cfg = replace(DoubleBollingerEngine5Config(), w_rsi_accel=15.0, w_volume=0.0, w_outer_expand=0.0)
    frames = build_cfg_frames(raw, cfg)
    scored = reweight(frames, cfg, 0.0)
    f = scored[TARGET_SYMBOL]
    q = f[pd.to_datetime(f['time']) == TARGET_TIME]
    if q.empty:
        raise SystemExit(f'target entry bar not found: {TARGET_SYMBOL} {TARGET_TIME}')
    r = q.iloc[0]
    band_r = float(r.inner_upper - r.inner_lower)
    stop_price = float(r.close - band_r)
    tp1_price = float(r.close + 2.0 * band_r)

    print('=== ENGINE 5 EXIT V4 TARGET REPLAY ===')
    print(f'symbol={TARGET_SYMBOL} entry_time={TARGET_TIME}')
    print(f'entry={float(r.close):.2f} score={float(r.entry_score):.2f} entry_gate={bool(r.entry_gate)}')
    print(f'entry_inner_upper={float(r.inner_upper):.2f} entry_inner_lower={float(r.inner_lower):.2f}')
    print(f'R_inner_band_width={band_r:.2f} ({band_r/float(r.close)*100.0:.3f}%)')
    print(f'1R_STOP={stop_price:.2f}')
    print(f'2R_TP1_50PCT={tp1_price:.2f}')

    packed_exits = pack_exit_events(raw, cfg)
    state_events = pack_state_events(frames)
    entry_events = pack_entry_events(scored)
    trades, collisions = simulate_v4(packed_exits, entry_events, state_events, THRESHOLD)
    t = trades[(trades['symbol'].astype(str) == TARGET_SYMBOL) & (pd.to_datetime(trades['entry_time']) == TARGET_TIME)]
    if t.empty:
        raise SystemExit('FAIL: target trade was not selected in portfolio simulation')
    x = t.iloc[0]
    print('--- V4 RESULT ---')
    print(x[['entry_time','entry_price','r_abs','r_pct','stop_price','tp1_price','first_tp_done','second_tp_done','exit_time','exit_price','pnl_pct','reason']].to_string())
    print(f'collisions={collisions}')
    if not bool(x.first_tp_done):
        raise SystemExit('FAIL: expected this known strong move to hit the 2R TP1; inspect R/time alignment')
    print('REPLAY PASS: corrected 2R TP1 was activated on the known 233740 case.')


if __name__ == '__main__':
    main()
