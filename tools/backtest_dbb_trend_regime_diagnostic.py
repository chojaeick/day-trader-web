from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.backtest_dbb_exit_lab import build_events, simulate
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT = Path('/home/ubuntu/day-trader-api')


def rolling_slope_pct(s: pd.Series, n: int) -> pd.Series:
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))

    def f(a):
        a = np.asarray(a, dtype=float)
        if len(a) != n or not np.isfinite(a).all():
            return np.nan
        m = float(a.mean())
        if m == 0.0:
            return np.nan
        slope = float(np.dot(xc, a - m) / denom)
        return slope / abs(m)

    return s.rolling(n, min_periods=n).apply(f, raw=True)


def baseline_trades(frames):
    return simulate(
        build_events(frames), frames,
        min_score=65.0,
        min_risk_pct=0.010,
        max_risk_pct=0.020,
        tp1_r=3.0,
        partial_fraction=0.75,
        structural_mode='CLOSE_BELOW_INNER_LOWER',
        runner_trail_pct=0.0,
        breakeven_after_tp1=True,
    )


def metric_row(name: str, t: pd.DataFrame) -> dict:
    r = summary(name, t)
    r['median_pct'] = round(float(t.pnl_pct.median()), 4) if not t.empty else 0.0
    return r


def build_context(frames, long_n=10, short_n=3):
    ctx = {}
    all_abs = []
    tmp = {}
    for sym, f in frames.items():
        z = f[['time','mid']].copy().sort_values('time')
        mid = pd.to_numeric(z['mid'], errors='coerce')
        z['slope_long'] = rolling_slope_pct(mid, long_n)
        z['slope_short'] = rolling_slope_pct(mid, short_n)
        tmp[sym] = z
        all_abs.extend(z['slope_long'].dropna().abs().tolist())

    # Data-driven flat threshold: bottom 20% of absolute long-slope magnitude.
    flat_thr = float(np.quantile(all_abs, 0.20)) if all_abs else 0.0

    for sym, z in tmp.items():
        def regime(r):
            a = r['slope_long']
            b = r['slope_short']
            if pd.isna(a) or pd.isna(b):
                return 'UNKNOWN'
            if abs(float(a)) <= flat_thr:
                if b > 0:
                    return 'FLAT_TURNING_UP'
                if b < 0:
                    return 'FLAT_TURNING_DOWN'
                return 'FLAT'
            if a > 0 and b > 0:
                return 'UP_CONTINUATION'
            if a > 0 and b <= 0:
                return 'UP_WEAKENING'
            if a < 0 and b < 0:
                return 'DOWN_CONTINUATION'
            return 'DOWN_RECOVERY'

        z['trend_regime'] = z.apply(regime, axis=1)
        ctx[sym] = z
    return ctx, flat_thr


def attach(trades, ctx):
    chunks = []
    x = trades.copy()
    x['entry_time'] = pd.to_datetime(x['entry_time'])
    for sym, g in x.groupby('symbol', sort=False):
        c = ctx[sym].rename(columns={'time':'ctx_time'}).sort_values('ctx_time')
        one = pd.merge_asof(
            g.sort_values('entry_time'),
            c[['ctx_time','slope_long','slope_short','trend_regime']],
            left_on='entry_time', right_on='ctx_time', direction='backward'
        )
        chunks.append(one)
    return pd.concat(chunks, ignore_index=True)


def main():
    raw = load_data()
    frames = build_frames_cached(raw, workers=2, rebuild=False)
    trades = baseline_trades(frames)
    ctx, flat_thr = build_context(frames, 10, 3)
    t = attach(trades, ctx)

    print(f'[BASELINE] trades={len(t)} gross={t.pnl_pct.sum():+.4f}% avg={t.pnl_pct.mean():+.4f}%')
    print(f'[TREND DEF] long=10-bar DBB-mid slope, short=3-bar DBB-mid slope, flat_abs_slope_threshold={flat_thr:.8f}')

    rows = []
    for regime, g in t.groupby('trend_regime', dropna=False):
        r = metric_row(str(regime), g)
        r['trend_regime'] = str(regime)
        rows.append(r)
    board = pd.DataFrame(rows).sort_values(['avg_pct','pf','gross_pct'], ascending=[False,False,False])

    cols = ['trend_regime','trades','win_rate','avg_pct','avg_win_pct','avg_loss_pct','gross_pct','pf','max_loss_pct','partial_rate','median_pct']
    print('\n=== CURRENT ENGINE PERFORMANCE BY DBB MID TREND REGIME ===')
    print(board[[c for c in cols if c in board.columns]].to_string(index=False))

    # Also report coarse long-trend direction only.
    coarse = t.copy()
    coarse['trend10_direction'] = np.where(coarse.slope_long > flat_thr, 'UP', np.where(coarse.slope_long < -flat_thr, 'DOWN', 'FLAT'))
    rows2 = []
    for regime, g in coarse.groupby('trend10_direction'):
        r = metric_row(str(regime), g)
        r['trend10_direction'] = str(regime)
        rows2.append(r)
    board2 = pd.DataFrame(rows2)
    print('\n=== COARSE 10-BAR DBB MID DIRECTION ===')
    print(board2[['trend10_direction','trades','win_rate','avg_pct','gross_pct','pf','max_loss_pct','partial_rate','median_pct']].to_string(index=False))

    # Strength quartiles within UP and DOWN, using absolute 10-bar slope.
    valid = coarse[coarse.trend10_direction.isin(['UP','DOWN'])].copy()
    if not valid.empty:
        valid['strength_q'] = pd.qcut(valid['slope_long'].abs(), 4, labels=['Q1_WEAK','Q2','Q3','Q4_STRONG'], duplicates='drop')
        rows3 = []
        for (d, q), g in valid.groupby(['trend10_direction','strength_q'], observed=True):
            r = metric_row(f'{d}|{q}', g)
            r['direction'] = d
            r['strength'] = str(q)
            rows3.append(r)
        board3 = pd.DataFrame(rows3)
        print('\n=== DIRECTION x TREND STRENGTH ===')
        print(board3[['direction','strength','trades','win_rate','avg_pct','gross_pct','pf','max_loss_pct','partial_rate','median_pct']].to_string(index=False))
        board3.to_csv(OUT / 'dbb_trend_regime_strength.csv', index=False)

    board.to_csv(OUT / 'dbb_trend_regime_detailed.csv', index=False)
    board2.to_csv(OUT / 'dbb_trend_regime_coarse.csv', index=False)
    t.to_csv(OUT / 'dbb_trend_regime_trades.csv', index=False)
    print('\n[NOTE] Diagnostic only: baseline trades are NOT filtered or re-simulated by trend regime.')
    print('[CSV] dbb_trend_regime_detailed.csv, dbb_trend_regime_coarse.csv, dbb_trend_regime_strength.csv')


if __name__ == '__main__':
    main()
