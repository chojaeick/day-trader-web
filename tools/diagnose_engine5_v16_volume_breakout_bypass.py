from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)

CASES = [
    ('257720', pd.Timestamp('2026-08-18 14:30:00+09:00'), 30),
    ('950260', pd.Timestamp('2026-08-21 10:00:00+09:00'), 30),
]


def fnum(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def enrich_volume_state(f: pd.DataFrame) -> pd.DataFrame:
    z = f.copy().sort_values('time').reset_index(drop=True)
    z['time'] = pd.to_datetime(z['time'])
    for c in ('open','high','low','close','volume','macd_slope','macd_slope_spread','rsi_slope','mid_slope8','entry_score'):
        if c in z.columns:
            z[c] = pd.to_numeric(z[c], errors='coerce')
    if 'volume' not in z.columns:
        z['volume'] = np.nan
    z['volume_prev'] = z['volume'].shift(1)
    z['volume_ratio_prev'] = z['volume'] / z['volume_prev'].replace(0, np.nan)
    z['close_prev'] = z['close'].shift(1)
    z['bar_return_pct'] = (z['close'] / z['close_prev'] - 1.0) * 100.0
    z['macd_slope_prev'] = z['macd_slope'].shift(1)
    z['macd_slope_d1'] = z['macd_slope'] - z['macd_slope_prev']
    z['rsi_slope_prev'] = z['rsi_slope'].shift(1)
    z['rsi_slope_d1'] = z['rsi_slope'] - z['rsi_slope_prev']
    z['spread_prev'] = z['macd_slope_spread'].shift(1)
    z['spread_d1'] = z['macd_slope_spread'] - z['spread_prev']

    # Context only: quantify whether the six completed 5m bars before each bar were flat/tight.
    prev_close = z['close'].shift(1)
    z['prev6_high'] = prev_close.rolling(6, min_periods=3).max()
    z['prev6_low'] = prev_close.rolling(6, min_periods=3).min()
    z['prev6_mid'] = (z['prev6_high'] + z['prev6_low']) / 2.0
    z['prev6_range_pct'] = (z['prev6_high'] - z['prev6_low']) / z['prev6_mid'].replace(0, np.nan) * 100.0

    z['volume_breakout_10x'] = (
        (z['volume_ratio_prev'] >= 10.0)
        & (z['bar_return_pct'] > 0)
        & (z['close'] > z['open'])
    ).fillna(False)
    z['momentum_accel'] = (
        (z['macd_slope'] > 0)
        & (z['macd_slope_d1'] > 0)
        & (z['macd_slope_spread'] > 0)
        & (z['rsi_slope'] > 0)
    ).fillna(False)
    z['bypass_hypothesis'] = (z['volume_breakout_10x'] & z['momentum_accel']).fillna(False)
    return z


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)

    all_rows = []
    print('=== ENGINE5 V16 VOLUME-BREAKOUT TREND-BYPASS DIAGNOSTIC ===')
    print('Diagnostic only. No strategy change. Tests the hypothesis: >=10x prior 5m volume + bullish breakout + MACD/RSI acceleration may bypass pre-existing trend confirmation.')

    for sym, entry_ts, lookback_min in CASES:
        z = enrich_volume_state(scored[sym])
        q = z[(z['time'] >= entry_ts - pd.Timedelta(minutes=lookback_min)) & (z['time'] <= entry_ts)].copy()
        cols = [
            'time','open','high','low','close','volume','volume_prev','volume_ratio_prev','bar_return_pct',
            'prev6_range_pct','trend_up','entry_gate','entry_score','macd_golden_cross',
            'macd_slope','macd_slope_prev','macd_slope_d1','macd_slope_spread','spread_d1',
            'rsi_slope','rsi_slope_prev','rsi_slope_d1','mid_slope8',
            'volume_breakout_10x','momentum_accel','bypass_hypothesis'
        ]
        cols = [c for c in cols if c in q.columns]
        print(f'\n--- {sym} actual_entry={entry_ts} ---')
        print(q[cols].to_string(index=False))

        hits = q[q['bypass_hypothesis']]
        if len(hits):
            first = hits.iloc[0]
            delta = (entry_ts - pd.Timestamp(first['time'])).total_seconds() / 60.0
            print(f'FIRST_BYPASS_CANDIDATE={first["time"]} price={first["close"]:.4f} volume_ratio={first["volume_ratio_prev"]:.3f} minutes_before_actual_entry={delta:.1f}')
        else:
            print('FIRST_BYPASS_CANDIDATE=NONE')

        q['symbol'] = sym
        q['actual_entry'] = entry_ts
        all_rows.append(q)

    out = pd.concat(all_rows, ignore_index=True)
    path = OUTDIR / 'v16_volume_breakout_bypass_diagnostic.csv'
    out.to_csv(path, index=False)
    print('\n[CSV]', path)


if __name__ == '__main__':
    main()
