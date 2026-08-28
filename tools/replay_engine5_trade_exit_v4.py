from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_engine5_fast_tuner_v4 import build_cfg_frames, pack_exit_events, pack_state_events
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, load_data

TARGET_SYMBOL = '233740'
TARGET_TIME = pd.Timestamp('2026-08-24 09:05:00+09:00')


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
    entry = float(r.close)
    band_r = float(r.inner_upper - r.inner_lower)
    stop_price = entry - band_r
    tp1_price = entry + 2.0 * band_r

    print('=== ENGINE 5 EXIT V4 TARGET REPLAY ===')
    print(f'symbol={TARGET_SYMBOL} entry_time={TARGET_TIME}')
    print(f'entry={entry:.2f} score={float(r.entry_score):.2f} entry_gate={bool(r.entry_gate)}')
    print(f'entry_inner_upper={float(r.inner_upper):.2f} entry_inner_lower={float(r.inner_lower):.2f}')
    print(f'R_inner_band_width={band_r:.2f} ({band_r/entry*100.0:.3f}%)')
    print(f'1R_STOP={stop_price:.2f}')
    print(f'2R_TP1_50PCT={tp1_price:.2f}')

    packed_exits = pack_exit_events(raw, cfg)
    state_events = pack_state_events(frames)
    current_state = {}
    remaining = 1.0
    realized = 0.0
    tp1_done = False
    tp2_done = False
    final = None

    def realize(frac, price):
        nonlocal remaining, realized
        frac = min(float(frac), remaining)
        realized += frac * (float(price) / entry - 1.0)
        remaining -= frac

    for ts, minute, rows in packed_exits:
        if ts in state_events:
            current_state.update(state_events[ts])
        if ts <= TARGET_TIME:
            continue
        if ts.date() != TARGET_TIME.date():
            break
        rr = rows.get(TARGET_SYMBOL)
        if rr is None:
            continue
        close, low, high, iu, il, ou = rr

        if minute >= FORCE_FLAT_MINUTE:
            final = (ts, close, 'SESSION_FORCE_FLAT')
            break

        if not tp1_done:
            if low <= stop_price:
                final = (ts, stop_price, 'INITIAL_1R_STOP')
                break
            if high >= tp1_price:
                realize(0.50, tp1_price)
                tp1_done = True
                print(f'TP1 time={ts} price={tp1_price:.2f} remaining={remaining:.3f}')
            continue

        trend_up, outer_expanding = current_state.get(TARGET_SYMBOL, (False, False))
        if (not trend_up) and np.isfinite(iu) and low <= iu:
            fill = iu if high >= iu else close
            final = (ts, fill, 'SIDEWAYS_INNER_UPPER_EXIT')
            break

        if (not tp2_done) and trend_up and outer_expanding and np.isfinite(ou) and high >= ou:
            realize(remaining * 0.50, ou)
            tp2_done = True
            print(f'TP2 time={ts} price={ou:.2f} remaining={remaining:.3f}')

        if tp2_done and np.isfinite(il) and close < il:
            final = (ts, close, 'INNER_LOWER_CLOSE_EXIT')
            break

    if final is None:
        raise SystemExit('FAIL: no final exit found for target trade')

    exit_time, exit_price, reason = final
    pnl = realized + remaining * (float(exit_price) / entry - 1.0)
    print('--- V4 RESULT ---')
    print(f'tp1_done={tp1_done} tp2_done={tp2_done}')
    print(f'exit_time={exit_time}')
    print(f'exit_price={float(exit_price):.2f}')
    print(f'reason={reason}')
    print(f'total_pnl_pct={pnl*100.0:+.4f}%')
    if not tp1_done:
        raise SystemExit('FAIL: expected this known strong move to hit the 2R TP1; inspect R/time alignment')
    print('REPLAY PASS: corrected 2R TP1 was activated on the known 233740 case.')


if __name__ == '__main__':
    main()
