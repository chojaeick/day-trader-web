from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10

# V11: gap-up confirmation layer derived from manual chart review.
# Keep V10 BUY/WAIT logic, V9 09:00-09:09 block (through V10), and V8 strict 1R/2R exits.
# For meaningful gap-ups during the first hour, do not buy a fading opening spike.
# Require the DBB trend to be up and MACD/RSI momentum to be maintained or re-accelerating.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v11_checkpoint.csv')
GAP_CONFIRM_PCT = 4.0
GAP_CONFIRM_END_MINUTE = 10 * 60  # special opening-gap confirmation applies before 10:00


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
    z['entry_gate_v11'] = z['entry_gate'].fillna(False) & (~gap_sensitive | gap_trend_confirmed)
    z['entry_gate'] = z['entry_gate_v11']
    return z


def build_cfg_frames(raw, cfg):
    frames = v10.build_cfg_frames(raw, cfg)
    out = {}
    for sym, f in frames.items():
        out[sym] = _apply_gap_confirmation(f, _daily_gap_map(raw[sym]))
    return out


def pack_entry_events(scored_frames):
    # V10 already refines BUY/WAIT; enforce the same 09:10 opening rule here.
    ev = v8.pack_entry_events(scored_frames)
    filtered = {}
    for ts, rows in ev.items():
        t = pd.Timestamp(ts)
        minute = t.hour * 60 + t.minute
        if minute >= 9 * 60 + 10:
            filtered[ts] = rows
    return filtered


def main():
    print('[ENGINE5 V11] V10 BUY/WAIT + 09:00-09:09 block + V8 strict 1R/2R exits; NEW GAP RULE: gap-up >=4% before 10:00 requires confirmed DBB uptrend and maintained/re-accelerating MACD+RSI.', flush=True)
    base.build_cfg_frames = build_cfg_frames
    base.pack_entry_events = pack_entry_events
    base.pack_exit_events = v8.base.pack_exit_events
    base.simulate_v4 = v8.v7.simulate_v7
    base.main()


if __name__ == '__main__':
    main()
