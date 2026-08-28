from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from tools.backtest_dbb_exit_lab import build_events, simulate
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT = Path('/home/ubuntu/day-trader-api')


def best_exit_trades(frames):
    events = build_events(frames)
    return simulate(
        events,
        frames,
        min_score=65.0,
        min_risk_pct=0.010,
        max_risk_pct=0.020,
        tp1_r=3.0,
        partial_fraction=0.75,
        structural_mode='CLOSE_BELOW_INNER_LOWER',
        runner_trail_pct=0.0,
        breakeven_after_tp1=True,
    )


def stat_row(name: str, t: pd.DataFrame) -> dict:
    s = summary(name, t)
    if t.empty:
        s.update({'median_pct': 0.0, 'p10_pct': 0.0, 'p90_pct': 0.0})
    else:
        s.update({
            'median_pct': round(float(t.pnl_pct.median()), 4),
            'p10_pct': round(float(t.pnl_pct.quantile(0.10)), 4),
            'p90_pct': round(float(t.pnl_pct.quantile(0.90)), 4),
        })
    return s


def print_table(title: str, rows: list[dict], cols: list[str] | None = None):
    print(f'\n=== {title} ===')
    if not rows:
        print('no rows')
        return
    df = pd.DataFrame(rows)
    if cols is None:
        cols = ['version','trades','win_rate','avg_pct','gross_pct','pf','max_loss_pct','median_pct']
    print(df[cols].to_string(index=False))


def phase_a_robustness(trades: pd.DataFrame):
    x = trades.copy()
    x['entry_time'] = pd.to_datetime(x['entry_time'])
    x['date'] = x['entry_time'].dt.date.astype(str)

    overall = stat_row('OVERALL', x)

    by_date = [stat_row(str(k), g) for k, g in x.groupby('date')]
    by_symbol = [stat_row(str(k), g) for k, g in x.groupby('symbol')]

    dates = sorted(x['date'].unique())
    cut = max(1, len(dates) // 2)
    first_dates = set(dates[:cut])
    half_rows = [
        stat_row('FIRST_HALF', x[x.date.isin(first_dates)]),
        stat_row('SECOND_HALF', x[~x.date.isin(first_dates)]),
    ]

    # Alternating-day split reduces dependence on one contiguous market regime.
    odd_dates = set(dates[::2])
    alt_rows = [
        stat_row('ODD_DATE_BUCKET', x[x.date.isin(odd_dates)]),
        stat_row('EVEN_DATE_BUCKET', x[~x.date.isin(odd_dates)]),
    ]

    cost_rows = []
    for cost_pct in [0.00, 0.03, 0.05, 0.10, 0.20]:
        c = x.copy()
        c['pnl_pct'] = c['pnl_pct'] - cost_pct
        r = stat_row(f'COST_{cost_pct:.2f}%_PER_TRADE', c)
        r['cost_pct'] = cost_pct
        cost_rows.append(r)

    print_table('PHASE A OVERALL', [overall])
    print_table('PHASE A BY DATE', by_date)
    print_table('PHASE A FIRST/SECOND HALF', half_rows)
    print_table('PHASE A ALTERNATING DATE BUCKETS', alt_rows)
    print_table('PHASE A COST STRESS', cost_rows)
    print_table('PHASE A BY SYMBOL', by_symbol)

    pd.DataFrame(by_date).to_csv(OUT / 'dbb_phase_a_by_date.csv', index=False)
    pd.DataFrame(by_symbol).to_csv(OUT / 'dbb_phase_a_by_symbol.csv', index=False)
    pd.DataFrame(cost_rows).to_csv(OUT / 'dbb_phase_a_cost_stress.csv', index=False)


def build_5m_context(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {}
    for sym, bars in raw.items():
        z = bars.copy().sort_values('time').set_index('time')
        r = z.resample('5min', label='right', closed='right').agg({
            'open':'first','high':'max','low':'min','close':'last','volume':'sum'
        }).dropna(subset=['open','high','low','close']).reset_index()
        close = pd.to_numeric(r['close'], errors='coerce').astype(float)

        mid = close.rolling(20, min_periods=20).mean()
        sd = close.rolling(20, min_periods=20).std(ddof=0)
        iu = mid + 0.5 * sd
        il = mid - 0.5 * sd
        width = (iu - il) / mid.abs().clip(lower=1e-9)

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        gap = macd - signal

        r['mid5'] = mid
        r['inner_upper5'] = iu
        r['inner_lower5'] = il
        r['mid5_slope3'] = mid.pct_change(3)
        r['iu5_slope3'] = iu.pct_change(3)
        r['width5_slope3'] = width.diff(3)
        r['macd5_gap'] = gap
        r['macd5_gap_delta'] = gap.diff()
        r['ret5_1'] = close.pct_change()
        r['ret5_3'] = close.pct_change(3)

        def trend(row):
            if pd.isna(row['mid5']) or pd.isna(row['mid5_slope3']) or pd.isna(row['iu5_slope3']):
                return 'UNKNOWN'
            p = float(row['close'])
            midv = float(row['mid5'])
            ms = float(row['mid5_slope3'])
            ius = float(row['iu5_slope3'])
            if p > midv and ms > 0 and ius > 0:
                return 'UP'
            if p < midv and ms < 0 and ius < 0:
                return 'DOWN'
            return 'FLAT'

        r['trend5'] = r.apply(trend, axis=1)
        out[sym] = r
    return out


def attach_entry_context(trades: pd.DataFrame, frames, ctx5):
    t = trades.copy()
    t['entry_time'] = pd.to_datetime(t['entry_time'])
    chunks = []

    for sym, tg in t.groupby('symbol', sort=False):
        one = tg.sort_values('entry_time').copy()
        c5 = ctx5[sym].sort_values('time').copy()
        # right-labeled 5m bars: only context whose timestamp is <= entry time is visible.
        one = pd.merge_asof(one, c5, left_on='entry_time', right_on='time', direction='backward')

        f = frames[sym][['time','gap_delta','rsi_slope1','regime']].sort_values('time').copy()
        f = f.rename(columns={'gap_delta':'macd1_gap_delta','rsi_slope1':'rsi1_slope1','regime':'regime1'})
        one = pd.merge_asof(one.sort_values('entry_time'), f, left_on='entry_time', right_on='time', direction='backward', suffixes=('','_1m'))
        chunks.append(one)

    out = pd.concat(chunks, ignore_index=True)
    out['macd5_state'] = 'MIXED'
    out.loc[(out.macd5_gap > 0) & (out.macd5_gap_delta > 0), 'macd5_state'] = 'BULL_ACCEL'
    out.loc[(out.macd5_gap < 0) & (out.macd5_gap_delta < 0), 'macd5_state'] = 'BEAR_ACCEL'
    out.loc[(out.macd5_gap > 0) & (out.macd5_gap_delta <= 0), 'macd5_state'] = 'BULL_DECEL'
    out.loc[(out.macd5_gap <= 0) & (out.macd5_gap_delta > 0), 'macd5_state'] = 'RECOVERING'
    return out


def grouped_stats(df: pd.DataFrame, key: str, prefix: str) -> list[dict]:
    rows = []
    for k, g in df.groupby(key, dropna=False):
        r = stat_row(f'{prefix}:{k}', g)
        r[key] = str(k)
        if 'mfe_pct' in g.columns:
            r['avg_mfe'] = round(float(g.mfe_pct.mean()), 4)
        if 'giveback_from_peak_pct' in g.columns:
            r['avg_giveback'] = round(float(g.giveback_from_peak_pct.mean()), 4)
        rows.append(r)
    return rows


def phase_b_macd(trades_ctx: pd.DataFrame):
    trend_rows = grouped_stats(trades_ctx, 'trend5', 'TREND5')
    macd_rows = grouped_stats(trades_ctx, 'macd5_state', 'MACD5')

    combo_rows = []
    for (tr, ms), g in trades_ctx.groupby(['trend5','macd5_state'], dropna=False):
        r = stat_row(f'{tr}|{ms}', g)
        r['trend5'] = str(tr)
        r['macd5_state'] = str(ms)
        r['avg_mfe'] = round(float(g.mfe_pct.mean()), 4) if 'mfe_pct' in g else 0.0
        combo_rows.append(r)

    cols = ['version','trades','win_rate','avg_pct','gross_pct','pf','max_loss_pct','median_pct']
    print_table('PHASE B 5M STRUCTURAL TREND', trend_rows, cols)
    print_table('PHASE B 5M MACD STATE', macd_rows, cols)
    print_table('PHASE B TREND x MACD', combo_rows, cols)

    pd.DataFrame(trend_rows).to_csv(OUT / 'dbb_phase_b_trend5.csv', index=False)
    pd.DataFrame(macd_rows).to_csv(OUT / 'dbb_phase_b_macd5.csv', index=False)
    pd.DataFrame(combo_rows).to_csv(OUT / 'dbb_phase_b_trend_x_macd.csv', index=False)
    trades_ctx.to_csv(OUT / 'dbb_phase_ab_trades_with_context.csv', index=False)


def parse_args():
    p = argparse.ArgumentParser(description='DBB Phase A robustness + Phase B 5m MACD context validation')
    p.add_argument('--workers', type=int, default=2)
    return p.parse_args()


def main():
    args = parse_args()
    raw = load_data()
    frames = build_frames_cached(raw, workers=args.workers, rebuild=False)
    trades = best_exit_trades(frames)
    print(f'[BASELINE] best-exit trades={len(trades)} gross={trades.pnl_pct.sum():+.4f}% avg={trades.pnl_pct.mean():+.4f}%')
    phase_a_robustness(trades)

    print('\n[PHASE B] building 5m context...', flush=True)
    ctx5 = build_5m_context(raw)
    trades_ctx = attach_entry_context(trades, frames, ctx5)
    phase_b_macd(trades_ctx)
    print('\nCSV saved: dbb_phase_a_*.csv, dbb_phase_b_*.csv, dbb_phase_ab_trades_with_context.csv')


if __name__ == '__main__':
    main()
