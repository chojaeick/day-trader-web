from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from tools.backtest_dbb_exit_lab import build_events, simulate
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT = Path('/home/ubuntu/day-trader-api')


def rolling_mid_slope_pct(mid: pd.Series, lookback: int) -> pd.Series:
    """Linear-regression slope across completed 1m DBB mid values, normalized by mean mid."""
    x = np.arange(lookback, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))

    def f(a):
        a = np.asarray(a, dtype=float)
        if len(a) != lookback or not np.isfinite(a).all():
            return np.nan
        mean = float(a.mean())
        if mean == 0.0:
            return np.nan
        slope = float(np.dot(xc, a - mean) / denom)
        return slope / abs(mean)

    return mid.rolling(lookback, min_periods=lookback).apply(f, raw=True)


def build_up_lookup(frames: dict[str, pd.DataFrame], lookback: int):
    lookup = {}
    stats = []
    for sym, f in frames.items():
        z = f[['time', 'mid']].copy().sort_values('time')
        z['mid_slope_pct'] = rolling_mid_slope_pct(pd.to_numeric(z['mid'], errors='coerce'), lookback)
        z['mid_net_pct'] = pd.to_numeric(z['mid'], errors='coerce').pct_change(lookback - 1)
        # Primary definition: the recent DBB central line itself is rising over the window.
        # Require both positive regression slope and positive start-to-end change so a noisy
        # hump with a positive fitted slope is not called an uptrend.
        z['uptrend'] = (z['mid_slope_pct'] > 0.0) & (z['mid_net_pct'] > 0.0)
        lookup[sym] = {r.time: bool(r.uptrend) for r in z.itertuples(index=False)}
        valid = z.dropna(subset=['mid_slope_pct', 'mid_net_pct'])
        stats.append({
            'symbol': sym,
            'bars': len(z),
            'valid_trend_bars': len(valid),
            'uptrend_bars': int(valid['uptrend'].sum()),
            'uptrend_bar_pct': round(float(valid['uptrend'].mean() * 100.0), 2) if len(valid) else 0.0,
        })
    return lookup, pd.DataFrame(stats)


def filter_events(events, up_lookup):
    out = []
    allowed_candidates = 0
    all_candidates = 0
    for ts, minute, row_by_symbol, base_candidates in events:
        all_candidates += len(base_candidates)
        cand = [r for r in base_candidates if up_lookup.get(str(r['symbol']), {}).get(ts, False)]
        allowed_candidates += len(cand)
        out.append((ts, minute, row_by_symbol, cand))
    return out, all_candidates, allowed_candidates


def run(events, frames):
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


def row(name: str, trades: pd.DataFrame) -> dict:
    s = summary(name, trades)
    if trades.empty:
        s.update({'median_pct': 0.0})
    else:
        s.update({'median_pct': round(float(trades.pnl_pct.median()), 4)})
    return s


def by_date(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    x = trades.copy()
    x['entry_time'] = pd.to_datetime(x['entry_time'])
    x['date'] = x['entry_time'].dt.date.astype(str)
    rows = []
    for d, g in x.groupby('date'):
        r = row(f'{name}:{d}', g)
        r['date'] = d
        rows.append(r)
    return pd.DataFrame(rows)


def by_symbol(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for sym, g in trades.groupby('symbol'):
        r = row(f'{name}:{sym}', g)
        r['symbol'] = sym
        rows.append(r)
    return pd.DataFrame(rows)


def exit_reasons(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    reason_col = 'reason' if 'reason' in trades.columns else ('exit_reason' if 'exit_reason' in trades.columns else None)
    if reason_col is None:
        return pd.DataFrame([{'version': name, 'note': 'no reason/exit_reason column'}])
    rows = []
    for reason, g in trades.groupby(reason_col):
        r = row(f'{name}:{reason}', g)
        r['exit_reason'] = reason
        rows.append(r)
    return pd.DataFrame(rows)


def cost_stress(name: str, trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cost in [0.00, 0.03, 0.05, 0.10, 0.20]:
        t = trades.copy()
        if not t.empty:
            t['pnl_pct'] = t['pnl_pct'] - cost
        r = row(f'{name}:COST_{cost:.2f}', t)
        r['cost_pct_per_trade'] = cost
        rows.append(r)
    return pd.DataFrame(rows)


def parse_args():
    p = argparse.ArgumentParser(description='Validate current DBB engine with 1m BB-mid uptrend-only entry permission')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--lookback', type=int, default=10)
    p.add_argument('--sensitivity', action='store_true', help='also test 5/15/20 bar midline windows')
    return p.parse_args()


def main():
    args = parse_args()
    raw = load_data()
    frames = build_frames_cached(raw, workers=args.workers, rebuild=False)
    events = build_events(frames)

    # Exit/risk is frozen to the current best candidate. Only entry permission changes.
    base = run(events, frames)
    print(f'[CONTROL] trades={len(base)} gross={base.pnl_pct.sum():+.4f}% avg={base.pnl_pct.mean():+.4f}%', flush=True)

    windows = [args.lookback]
    if args.sensitivity:
        windows = sorted(set([5, args.lookback, 15, 20]))

    board = [row('CONTROL_NO_TREND_FILTER', base)]
    primary_trades = None

    for lookback in windows:
        up_lookup, bar_stats = build_up_lookup(frames, lookback)
        filtered_events, all_cand, allowed_cand = filter_events(events, up_lookup)
        trades = run(filtered_events, frames)
        name = f'MID{lookback}_UP_ONLY'
        r = row(name, trades)
        r['lookback'] = lookback
        r['base_signal_candidates'] = all_cand
        r['allowed_signal_candidates'] = allowed_cand
        r['candidate_pass_pct'] = round((allowed_cand / all_cand * 100.0), 2) if all_cand else 0.0
        board.append(r)
        print(f'[{name}] candidates={allowed_cand}/{all_cand} ({r["candidate_pass_pct"]:.2f}%) trades={len(trades)} gross={r["gross_pct"]:+.4f}% avg={r["avg_pct"]:+.4f}% win={r["win_rate"]:.2f}% pf={r["pf"]:.3f}', flush=True)

        if lookback == args.lookback:
            primary_trades = trades.copy()
            bar_stats.to_csv(OUT / f'dbb_midtrend_{lookback}_bar_stats.csv', index=False)
            by_date(name, trades).to_csv(OUT / f'dbb_midtrend_{lookback}_by_date.csv', index=False)
            by_symbol(name, trades).to_csv(OUT / f'dbb_midtrend_{lookback}_by_symbol.csv', index=False)
            exit_reasons(name, trades).to_csv(OUT / f'dbb_midtrend_{lookback}_exit_reasons.csv', index=False)
            cost_stress(name, trades).to_csv(OUT / f'dbb_midtrend_{lookback}_cost_stress.csv', index=False)

    board_df = pd.DataFrame(board)
    cols = ['version','trades','win_rate','avg_pct','avg_win_pct','avg_loss_pct','gross_pct','pf','max_loss_pct','partial_rate','median_pct']
    print('\n=== DBB MIDLINE UPTREND ENTRY A/B ===')
    print(board_df[[c for c in cols if c in board_df.columns]].to_string(index=False))

    board_df.to_csv(OUT / 'dbb_midtrend_entry_ab.csv', index=False)
    base.to_csv(OUT / 'dbb_midtrend_control_trades.csv', index=False)
    if primary_trades is not None:
        primary_trades.to_csv(OUT / f'dbb_midtrend_{args.lookback}_up_only_trades.csv', index=False)

    print('\n[DEFINITION] UP = completed 1m DBB mid regression slope > 0 AND mid_now > mid_(N-1)')
    print('[FROZEN EXIT] risk 1-2%, TP1 3R, 75% partial, BE after TP1, no trail, close<inner-lower structural exit')
    print('[PATH] full re-simulation from flat; this is NOT post-hoc filtering')
    print(f'[CSV] {OUT / "dbb_midtrend_entry_ab.csv"}')


if __name__ == '__main__':
    main()
