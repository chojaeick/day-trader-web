from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5

# New namespace: V6 changes R/stop semantics and momentum-fade timing.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v6_checkpoint.csv')


def _finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def pack_exit_events(raw, cfg):
    """Pack 1m price/bands plus 1m MACD/RSI momentum for fast exit checks."""
    by_time = {}
    eng = DoubleBollingerEngine5(cfg)
    for sym, bars in sorted(raw.items()):
        f = bars.copy().sort_values('time').reset_index(drop=True)
        f['time'] = pd.to_datetime(f['time'])
        close = pd.to_numeric(f['close'], errors='coerce').astype(float)
        mid = close.rolling(cfg.bb_period).mean()
        std = close.rolling(cfg.bb_period).std(ddof=0)
        f['inner_upper_1m'] = mid + cfg.inner_sigma * std
        f['inner_lower_1m'] = mid - cfg.inner_sigma * std
        f['outer_upper_1m'] = mid + cfg.outer_sigma * std

        macd, signal = eng._macd(close)
        spread = macd.diff() - signal.diff()
        rsi = eng._rsi(close, cfg.rsi_period)
        f['macd_spread_1m'] = spread
        f['rsi_slope_1m'] = rsi.diff()

        cols = [
            'time', 'close', 'low', 'high', 'inner_upper_1m', 'inner_lower_1m',
            'outer_upper_1m', 'macd_spread_1m', 'rsi_slope_1m',
        ]
        for r in f[cols].itertuples(index=False, name=None):
            ts = pd.Timestamp(r[0])
            by_time.setdefault(ts, {})[sym] = (
                float(r[1]), float(r[2]), float(r[3]), _finite(r[4]), _finite(r[5]),
                _finite(r[6]), _finite(r[7]), _finite(r[8]),
            )
    return [(ts, ts.hour * 60 + ts.minute, rows) for ts, rows in sorted(by_time.items())]


def pack_entry_events(scored_frames):
    """Keep TP unit equal to the full 5m inner-band width at entry.

    If a completed 5m close is already above outer-upper, only the stop gets a
    structural extension back to inner-upper. TP1 remains +2 raw band-widths.
    """
    ev = {}
    for sym, f in scored_frames.items():
        if 'entry_gate' not in f.columns:
            raise RuntimeError('Engine 5 frame missing entry_gate; corrected persistence gate is not deployed')
        q = f[f['entry_gate']].copy()
        cols = [
            'time', 'close', 'entry_score', 'macd_slope_spread_strength',
            'rsi_slope_strength', 'inner_upper', 'inner_lower', 'outer_upper', 'mid',
        ]
        for r in q[cols].itertuples(index=False, name=None):
            ts = pd.Timestamp(r[0])
            close = float(r[1])
            iu, il, ou, mid = _finite(r[5]), _finite(r[6]), _finite(r[7]), _finite(r[8])
            band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
            if not np.isfinite(band_r) or band_r <= 0:
                continue
            extended_entry = bool(np.isfinite(ou) and close > ou)
            stop_dist = band_r
            if extended_entry and np.isfinite(iu):
                stop_dist = max(band_r, close - iu)
            ev.setdefault(ts, []).append((
                sym, close, float(r[2]), _finite(r[3]), _finite(r[4]),
                band_r, stop_dist, iu, il, ou, mid, extended_entry,
            ))
    return ev


def simulate_v6(packed_exits, entry_events, state_events, threshold: float):
    """Engine 5 exit V6.

    - TP unit R = full 5m inner-band width at entry.
    - TP1 = entry + 2R, sell 50% original. TP1 has priority over ordinary fade.
    - Initial stop = entry - R. Only if the completed 5m entry close is already
      above outer-upper, widen stop structurally to at least inner-upper.
    - After TP1, continuing 5m uptrend + outer expansion: 1m outer-upper touch
      sells half the remainder (25% original).
    - After TP1, two consecutive 1m bars with BOTH MACD slope-spread <= 0 and
      RSI slope <= 0 exit the remainder immediately (fast trend-turn protection).
    - Before TP1, ordinary 1m fade cannot steal the trade. Only a clear 5m
      collapse (trend_up false and at least two of mid-slope/spread/RSI <= 0)
      can close early.
    - Final runner also exits on 1m close below inner-lower.
    - No cooldown: a fresh entry_gate can re-enter after an exit.
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
            'extended_entry': pos['extended_entry'],
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
                pos['fast_fade_streak'] = pos['fast_fade_streak'] + 1 if fast_fade_now else 0

                if minute >= base.FORCE_FLAT_MINUTE:
                    close_record(close, ts, 'SESSION_FORCE_FLAT')
                elif not pos['tp1_done']:
                    # Conservative same-bar ambiguity: stop before target.
                    if low <= pos['stop_price']:
                        close_record(pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                    elif high >= pos['tp1_price']:
                        realize(0.50, pos['tp1_price'])
                        pos['tp1_done'] = True
                        pos['tp1_time'] = ts
                        pos['fast_fade_streak'] = 0
                    elif clear_5m_collapse:
                        close_record(close, ts, 'PRE_TP1_CLEAR_TREND_COLLAPSE')
                else:
                    # Fast turn protection after TP1: do not wait for the 5m bar
                    # to confirm if both 1m momentum measures deteriorate twice.
                    if pos['fast_fade_streak'] >= 2:
                        close_record(close, ts, 'FAST_1M_MOMENTUM_FADE_EXIT')
                    else:
                        if (not pos['tp2_done']) and trend_up and outer_expanding and np.isfinite(ou) and high >= ou:
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
                        'fast_fade_streak': 0,
                    }
                    last_price = close

    if pos is not None and last_price is not None and last_ts is not None:
        close_record(last_price, last_ts, 'END_OF_DATA')

    return pd.DataFrame(trades), collisions


# Monkeypatch the existing fast tuner orchestration so the proven caching,
# configuration sweep and checkpoint flow are reused without duplicating it.
base.pack_exit_events = pack_exit_events
base.pack_entry_events = pack_entry_events
base.simulate_v4 = simulate_v6


def main():
    print('[ENGINE5 EXIT V6] R=raw inner-band width; TP1=+2R 50%; stop structurally widens only for close>outer-upper; post-TP1 1m MACD+RSI two-bar fade exits; pre-TP1 fade cannot steal TP1.', flush=True)
    base.main()


if __name__ == '__main__':
    main()
