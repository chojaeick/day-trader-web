from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v6 as v6
import tools.backtest_dbb_engine5_fast_tuner_v4 as base

# New namespace: V7 changes post-TP1 fast-fade arming semantics.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v7_checkpoint.csv')


def simulate_v7(packed_exits, entry_events, state_events, threshold: float):
    """Engine 5 exit V7.

    Keeps V6 entry/R/stop semantics, but prevents a normal pullback immediately
    after TP1 from closing the remainder too early.

    - R = full 5m inner-band width at entry.
    - TP1 = entry + 2R, sell 50% original.
    - Initial stop = entry - R; only a completed 5m close above outer-upper may
      widen the stop structurally back toward inner-upper.
    - After TP1, fast 1m fade protection is DISARMED until continuation is proven.
      It arms when either:
        a) price makes a fresh post-TP1 high above the TP1 bar high, or
        b) price touches the current 1m outer-upper while 5m trend_up and
           outer_expanding are both true.
    - Once armed, two consecutive 1m bars with BOTH MACD slope-spread <= 0 and
      RSI slope <= 0 exit all remaining shares.
    - TP2: after TP1, continuing 5m uptrend + outer expansion and outer-upper
      touch sells half the remainder (25% original).
    - Final runner exits on 1m close below inner-lower.
    - Before TP1, only a clear 5m collapse can close early.
    - No cooldown; fresh entry_gate may re-enter after exit.
    """
    pos = None
    trades = []
    collisions = 0
    current_state = {}
    last_price = None
    last_ts = None

    def realize(fraction_of_original, price):
        nonlocal pos
        fraction = min(float(fraction_of_original), pos['remaining'])
        if fraction <= 0:
            return
        pos['realized'] += fraction * (float(price) / pos['entry_price'] - 1.0)
        pos['remaining'] -= fraction

    def close_record(price, ts, reason):
        nonlocal pos
        pnl = pos['realized'] + pos['remaining'] * (float(price) / pos['entry_price'] - 1.0)
        trades.append({
            'symbol': pos['symbol'], 'entry_time': pos['entry_time'], 'exit_time': pd.Timestamp(ts),
            'entry_price': pos['entry_price'], 'exit_price': float(price), 'entry_score': pos['entry_score'],
            'r_abs': pos['r_abs'], 'raw_band_r': pos['r_abs'], 'r_pct': pos['r_abs'] / pos['entry_price'] * 100.0,
            'stop_dist': pos['stop_dist'], 'stop_price': pos['stop_price'], 'tp1_price': pos['tp1_price'],
            'extended_entry': pos['extended_entry'], 'fade_armed': pos['fade_armed'],
            'pnl_pct': pnl * 100.0, 'first_tp_done': pos['tp1_done'], 'second_tp_done': pos['tp2_done'],
            'partial_done': pos['tp1_done'], 'extra_tp_count': int(pos['tp2_done']),
            'remaining_before_final': pos['remaining'], 'reason': reason,
        })
        pos = None

    for ts, minute, rows in packed_exits:
        last_ts = ts
        if ts in state_events:
            current_state.update(state_events[ts])

        if pos is not None:
            rr = rows.get(pos['symbol'])
            if rr is not None:
                close, low, high, iu, il, ou, spread1, rsi_slope1 = rr
                last_price = close
                trend_up, outer_expanding, mid_slope8, spread5, rsi_slope5 = current_state.get(
                    pos['symbol'], (False, False, np.nan, np.nan, np.nan)
                )
                fade_votes_5m = (
                    int(np.isfinite(mid_slope8) and mid_slope8 <= 0)
                    + int(np.isfinite(spread5) and spread5 <= 0)
                    + int(np.isfinite(rsi_slope5) and rsi_slope5 <= 0)
                )
                clear_5m_collapse = (not trend_up) and fade_votes_5m >= 2
                fast_fade_now = (
                    np.isfinite(spread1) and spread1 <= 0
                    and np.isfinite(rsi_slope1) and rsi_slope1 <= 0
                )

                if minute >= base.FORCE_FLAT_MINUTE:
                    close_record(close, ts, 'SESSION_FORCE_FLAT')
                elif not pos['tp1_done']:
                    if low <= pos['stop_price']:
                        close_record(pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                    elif high >= pos['tp1_price']:
                        realize(0.50, pos['tp1_price'])
                        pos['tp1_done'] = True
                        pos['tp1_time'] = ts
                        pos['tp1_bar_high'] = high
                        pos['post_tp1_high'] = high
                        pos['fade_armed'] = False
                        pos['fast_fade_streak'] = 0
                    elif clear_5m_collapse:
                        close_record(close, ts, 'PRE_TP1_CLEAR_TREND_COLLAPSE')
                else:
                    # First prove continuation after TP1. A routine pullback immediately
                    # after the 2R scale-out must not activate the fast-fade exit.
                    prior_post_high = pos['post_tp1_high']
                    fresh_post_tp1_high = high > max(pos['tp1_bar_high'], prior_post_high)
                    outer_continuation = (
                        trend_up and outer_expanding and np.isfinite(ou) and high >= ou
                    )
                    if fresh_post_tp1_high or outer_continuation:
                        pos['fade_armed'] = True
                    pos['post_tp1_high'] = max(prior_post_high, high)

                    if pos['fade_armed']:
                        pos['fast_fade_streak'] = pos['fast_fade_streak'] + 1 if fast_fade_now else 0
                    else:
                        pos['fast_fade_streak'] = 0

                    if pos['fade_armed'] and pos['fast_fade_streak'] >= 2:
                        close_record(close, ts, 'FAST_1M_MOMENTUM_FADE_EXIT')
                    else:
                        if (not pos['tp2_done']) and outer_continuation:
                            realize(pos['remaining'] * 0.50, ou)
                            pos['tp2_done'] = True
                            pos['tp2_time'] = ts

                        if pos is not None and pos['tp2_done'] and np.isfinite(il) and close < il:
                            close_record(close, ts, 'INNER_LOWER_CLOSE_EXIT')

        if pos is None and minute < base.NO_ENTRY_MINUTE:
            cands = entry_events.get(ts)
            if cands:
                eligible = [c for c in cands if c[2] >= float(threshold)]
                if eligible:
                    if len(eligible) > 1:
                        collisions += 1
                    sym, close, score, ms, rs, band_r, stop_dist, entry_iu, entry_il, entry_ou, entry_mid, extended_entry = max(
                        eligible,
                        key=lambda c: (c[2], c[3] if np.isfinite(c[3]) else -1e9, c[4] if np.isfinite(c[4]) else -1e9, c[0])
                    )
                    pos = {
                        'symbol': sym, 'entry_time': pd.Timestamp(ts), 'entry_price': close, 'entry_score': score,
                        'r_abs': band_r, 'stop_dist': stop_dist,
                        'entry_inner_upper': entry_iu, 'entry_inner_lower': entry_il,
                        'entry_outer_upper': entry_ou, 'entry_mid': entry_mid,
                        'extended_entry': extended_entry,
                        'stop_price': close - stop_dist,
                        'tp1_price': close + 2.0 * band_r,
                        'remaining': 1.0, 'realized': 0.0,
                        'tp1_done': False, 'tp2_done': False,
                        'tp1_time': None, 'tp2_time': None,
                        'tp1_bar_high': np.nan, 'post_tp1_high': -np.inf,
                        'fade_armed': False, 'fast_fade_streak': 0,
                    }
                    last_price = close

    if pos is not None and last_price is not None and last_ts is not None:
        close_record(last_price, last_ts, 'END_OF_DATA')

    return pd.DataFrame(trades), collisions


# Reuse V6 data packing/R logic, but replace only exit state machine.
base.pack_exit_events = v6.pack_exit_events
base.pack_entry_events = v6.pack_entry_events
base.simulate_v4 = simulate_v7


def main():
    print('[ENGINE5 EXIT V7] TP1=+2R 50%; fast 1m fade is armed only after post-TP1 continuation (fresh high or trend+outer-upper touch), then two-bar MACD+RSI fade exits; no cooldown.', flush=True)
    base.main()


if __name__ == '__main__':
    main()
