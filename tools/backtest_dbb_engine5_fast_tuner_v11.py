from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10

# IMPORTANT: capture the original V4 frame builder BEFORE monkey-patching base.
# Calling v10.build_cfg_frames after replacing base.build_cfg_frames can change the
# composition path (and can recurse through the shared module object). V11 must be
# a strict subset of V10 entry events, never a different entry engine.
ORIG_BUILD_CFG_FRAMES = base.build_cfg_frames

base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v11_checkpoint.csv')
GAP_CONFIRM_PCT = 4.0
GAP_CONFIRM_END_MINUTE = 10 * 60
OPEN_ENTRY_MINUTE = 9 * 60 + 10


def _daily_gap_map(raw_bars: pd.DataFrame) -> dict:
    d = raw_bars.copy().sort_values('time')
    d['time'] = pd.to_datetime(d['time'])
    d['date'] = d['time'].dt.date
    days = []
    for day, g in d.groupby('date', sort=True):
        days.append((day, float(g.iloc[0]['open']), float(g.iloc[-1]['close'])))
    out = {}
    for i, (day, day_open, _) in enumerate(days):
        if i == 0:
            out[day] = np.nan
            continue
        prev_close = days[i - 1][2]
        out[day] = (day_open / prev_close - 1.0) * 100.0 if prev_close else np.nan
    return out


def _apply_gap_confirmation(frame: pd.DataFrame, gap_map: dict) -> pd.DataFrame:
    z = frame.copy()
    ts = pd.to_datetime(z['time'])
    z['gap_pct'] = ts.dt.date.map(gap_map).astype(float)
    minute = ts.dt.hour * 60 + ts.dt.minute

    spread = pd.to_numeric(z['macd_slope_spread'], errors='coerce')
    rsi_slope = pd.to_numeric(z['rsi_slope'], errors='coerce')
    prev_spread = spread.shift(1)
    prev_rsi_slope = rsi_slope.shift(1)

    # For >=4% gap-ups before 10:00, reject a fading opening spike.
    macd_maintained = (
        (spread > 0)
        & ((prev_spread <= 0) | (spread >= prev_spread))
    ).fillna(False)
    rsi_maintained = (
        (rsi_slope > 0)
        & ((prev_rsi_slope <= 0) | (rsi_slope >= prev_rsi_slope * 0.70))
    ).fillna(False)
    gap_trend_confirmed = (
        z['trend_up'].fillna(False)
        & z['gate_macd_context'].fillna(False)
        & macd_maintained
        & rsi_maintained
    )
    gap_sensitive = (
        (z['gap_pct'] >= GAP_CONFIRM_PCT)
        & (minute < GAP_CONFIRM_END_MINUTE)
    ).fillna(False)

    z['gap_sensitive_open'] = gap_sensitive
    z['gap_trend_confirmed'] = gap_trend_confirmed
    # CRITICAL: V11 can only remove V10 entries; it can never create a new one.
    v10_gate = z['entry_gate'].fillna(False)
    z['entry_gate_v11'] = v10_gate & (~gap_sensitive | gap_trend_confirmed)
    z['entry_gate'] = z['entry_gate_v11']
    return z


def build_cfg_frames(raw, cfg):
    # Reproduce V10 exactly from the immutable original V4 frames, then add only
    # the V11 gap filter. Do not call v10.build_cfg_frames through patched base.
    raw_frames = ORIG_BUILD_CFG_FRAMES(raw, cfg)
    out = {}
    for sym, f in raw_frames.items():
        f10 = v10._refine_entry_frame(f)
        out[sym] = _apply_gap_confirmation(f10, _daily_gap_map(raw[sym]))
    return out


def pack_entry_events(scored_frames):
    ev = v8.pack_entry_events(scored_frames)
    filtered = {}
    for ts, rows in ev.items():
        t = pd.Timestamp(ts)
        minute = t.hour * 60 + t.minute
        if minute >= OPEN_ENTRY_MINUTE:
            filtered[ts] = rows
    return filtered


def main():
    print('[ENGINE5 V11 FIXED] exact V10 BUY/WAIT baseline + 09:00-09:09 block + >=4% gap-up pre-10:00 fade confirmation; V11 entry set is guaranteed to be a subset of V10.', flush=True)
    base.build_cfg_frames = build_cfg_frames
    base.pack_entry_events = pack_entry_events
    base.pack_exit_events = v8.base.pack_exit_events
    base.simulate_v4 = v8.v7.simulate_v7
    base.main()


if __name__ == '__main__':
    main()
