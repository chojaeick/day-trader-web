from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import to_5m
from tools.backtest_dbb_kr_v2_v21_v22 import load_data


def bands_1m(bars: pd.DataFrame, cfg: DoubleBollingerEngine5Config) -> pd.DataFrame:
    f = bars.copy().sort_values('time').reset_index(drop=True)
    f['time'] = pd.to_datetime(f['time'])
    c = pd.to_numeric(f['close'], errors='coerce').astype(float)
    mid = c.rolling(cfg.bb_period).mean()
    std = c.rolling(cfg.bb_period).std(ddof=0)
    f['mid_1m'] = mid
    f['inner_upper_1m'] = mid + cfg.inner_sigma * std
    f['inner_lower_1m'] = mid - cfg.inner_sigma * std
    f['outer_upper_1m'] = mid + cfg.outer_sigma * std
    return f


def fmt(v, n=2):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return 'nan'
    return f'{float(v):.{n}f}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='484810')
    ap.add_argument('--date', default='2026-08-10')
    ap.add_argument('--entry-time', default='11:15')
    ap.add_argument('--entry-price', type=float, default=16140.0)
    ap.add_argument('--fade-ratio', type=float, default=0.50,
                    help='mid_slope8 <= recent 3-bar positive peak * ratio counts as DBB trend fading')
    args = ap.parse_args()

    raw = load_data()
    if args.symbol not in raw:
        raise SystemExit(f'symbol not found: {args.symbol}')

    cfg = DoubleBollingerEngine5Config()
    b1 = bands_1m(raw[args.symbol], cfg)
    day = b1[b1.time.dt.strftime('%Y-%m-%d') == args.date].copy()
    if day.empty:
        raise SystemExit(f'no 1m data for {args.symbol} {args.date}')

    entry_rows = day[day.time.dt.strftime('%H:%M') == args.entry_time]
    if entry_rows.empty:
        raise SystemExit(f'entry minute not found: {args.date} {args.entry_time}')
    entry_ts = pd.Timestamp(entry_rows.iloc[0].time)

    eng = DoubleBollingerEngine5(cfg)
    e5 = eng.enrich(to_5m(raw[args.symbol]))
    e5['time'] = pd.to_datetime(e5['time'])
    e5 = e5[e5.time <= entry_ts].copy()
    if e5.empty:
        raise SystemExit('no completed 5m Engine5 row at entry')
    er = e5.iloc[-1]

    print('=== ENGINE 5 ENTRY CHECK ===')
    print(f'symbol={args.symbol} date={args.date} entry_time={entry_ts} entry_price={args.entry_price:.2f}')
    print(
        '5m_completed=' + str(er.time) +
        f' close={fmt(er.close)} mid={fmt(er.mid)} inner_upper={fmt(er.inner_upper)} outer_upper={fmt(er.outer_upper)}'
    )
    print(
        f'trend_up={bool(er.trend_up)} mid_slope8={fmt(er.mid_slope8,4)} '
        f'macd={fmt(er.macd,4)} signal={fmt(er.macd_signal,4)} golden={bool(er.macd_golden_cross)} '
        f'macd_slope_spread={fmt(er.macd_slope_spread,4)} strength={fmt(er.macd_slope_spread_strength,3)}'
    )
    print(
        f'rsi={fmt(er.rsi,2)} rsi_slope={fmt(er.rsi_slope,3)} rsi_accel={fmt(er.rsi_accel,3)} '
        f'rsi_strength={fmt(er.rsi_slope_strength,3)} entry_score={fmt(er.entry_score,2)}'
    )

    post = day[day.time > entry_ts].copy()
    if post.empty:
        raise SystemExit('no post-entry data')

    # Completed 5m context is causal: each 5m row is labeled at bucket end.
    day5 = eng.enrich(to_5m(raw[args.symbol]))
    day5['time'] = pd.to_datetime(day5['time'])
    day5 = day5[day5.time.dt.strftime('%Y-%m-%d') == args.date].copy().sort_values('time')
    day5['mid_slope_peak3'] = day5['mid_slope8'].where(day5['mid_slope8'] > 0).rolling(3, min_periods=1).max().shift(1)

    remaining = 1.0
    realized = 0.0
    tp1 = None
    exit_event = None
    max_high = args.entry_price
    max_high_ts = entry_ts
    min_low = args.entry_price
    min_low_ts = entry_ts

    print('\n=== NEW EXIT V1 REPLAY ===')
    print('rules: outer-upper touch(high>=OU)->50% TP; DBB trend fade/loss->all exit; 1m close<inner-upper->structural stop')

    for _, r in post.iterrows():
        ts = pd.Timestamp(r.time)
        high = float(r.high)
        low = float(r.low)
        close = float(r.close)
        iu = float(r.inner_upper_1m) if pd.notna(r.inner_upper_1m) else np.nan
        ou = float(r.outer_upper_1m) if pd.notna(r.outer_upper_1m) else np.nan

        if high > max_high:
            max_high, max_high_ts = high, ts
        if low < min_low:
            min_low, min_low_ts = low, ts

        ctxs = day5[day5.time <= ts]
        ctx = ctxs.iloc[-1] if len(ctxs) else None

        # Profit event first: unlike the old full-candle-above rule, a wick/touch counts.
        if tp1 is None and np.isfinite(ou) and high >= ou:
            px = float(ou)  # conservative: book at the band touch, not at candle high/close
            frac = 0.50
            realized += frac * (px / args.entry_price - 1.0)
            remaining -= frac
            tp1 = (ts, px, ou)
            print(f'TP1_50 time={ts} price={px:.2f} outer_upper={ou:.2f} pnl_leg={(px/args.entry_price-1)*100:+.3f}%')

        # 5m trend fading/loss: DBB-mid slope is primary; MACD/RSI confirm fading.
        trend_fade = False
        fade_detail = ''
        if ctx is not None and pd.notna(ctx.mid_slope8):
            slope = float(ctx.mid_slope8)
            peak = float(ctx.mid_slope_peak3) if pd.notna(ctx.mid_slope_peak3) else np.nan
            trend_lost = slope <= 0.0
            slope_faded = np.isfinite(peak) and peak > 0 and slope <= peak * args.fade_ratio
            momentum_faded = (float(ctx.macd_slope_spread) <= 0.0 if pd.notna(ctx.macd_slope_spread) else False) or \
                             (float(ctx.rsi_slope) <= 0.0 if pd.notna(ctx.rsi_slope) else False)
            trend_fade = trend_lost or (slope_faded and momentum_faded)
            fade_detail = (
                f'5m={ctx.time} slope={slope:.4f} peak3={fmt(peak,4)} '
                f'macd_spread={fmt(ctx.macd_slope_spread,4)} rsi_slope={fmt(ctx.rsi_slope,3)}'
            )

        if trend_fade:
            exit_event = ('TREND_FADE_FULL_EXIT', ts, close, fade_detail)
            break

        # Structural failure. Use completed 1m close, not a wick, to avoid noise.
        if np.isfinite(iu) and close < iu:
            exit_event = ('INNER_UPPER_CLOSE_BREAK', ts, close, f'inner_upper_1m={iu:.2f}')
            break

    if exit_event is None:
        r = post.iloc[-1]
        exit_event = ('END_OF_DAY', pd.Timestamp(r.time), float(r.close), '')

    reason, xt, xp, detail = exit_event
    total = realized + remaining * (xp / args.entry_price - 1.0)
    print(f'EXIT time={xt} price={xp:.2f} reason={reason} remaining={remaining:.3f} {detail}')
    print(f'MFE high={max_high:.2f} time={max_high_ts} mfe={(max_high/args.entry_price-1)*100:+.3f}%')
    print(f'MAE low={min_low:.2f} time={min_low_ts} mae={(min_low/args.entry_price-1)*100:+.3f}%')
    print(f'NEW_EXIT_V1 total_pnl={total*100:+.3f}% realized_partial={realized*100:+.3f}%')
    print('OLD_RECORDED pnl=-1.000% exit=2026-08-10 13:51 price=15978.60 reason=INITIAL_STOP')


if __name__ == '__main__':
    main()
