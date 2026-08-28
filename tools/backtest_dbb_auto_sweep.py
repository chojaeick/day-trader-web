from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path

import pandas as pd

from live_server.double_bollinger_v22 import DoubleBollingerV22Config, DoubleBollingerV22ExitPolicy
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, Pos, close_trade, load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT_DIR = Path('/home/ubuntu/day-trader-api')


def build_fast_events(frames):
    """Build immutable-ish event rows once so every sweep candidate reuses them."""
    by_time = {}
    for sym, frame in frames.items():
        cols = [
            'time', 'open', 'high', 'low', 'close', 'score', 'stage',
            'base_entry', 'regime', 'inner_lower',
        ]
        for r in frame[cols].to_dict('records'):
            ts = r['time']
            r['symbol'] = sym
            by_time.setdefault(ts, []).append(r)

    events = []
    for ts in sorted(by_time):
        minute = ts.hour * 60 + ts.minute
        rows = by_time[ts]
        row_by_symbol = {r['symbol']: r for r in rows}
        base_candidates = [r for r in rows if bool(r['base_entry'])]
        events.append((ts, minute, row_by_symbol, base_candidates))
    return events


def simulate(events, frames, *, block_falling: bool, min_score: float,
             min_risk_pct: float, max_risk_pct: float,
             tp1_r_multiple: float, partial_fraction: float):
    cfg = DoubleBollingerV22Config(
        min_risk_pct=min_risk_pct,
        max_risk_pct=max_risk_pct,
        tp1_r_multiple=tp1_r_multiple,
        partial_fraction=partial_fraction,
    )
    policy = DoubleBollingerV22ExitPolicy(cfg)
    pos = None
    trades = []
    tp1 = None
    risk_pct = None

    for ts, minute, row_by_symbol, base_candidates in events:
        if pos is not None:
            match = row_by_symbol.get(pos.symbol)
            if match is not None:
                p = float(match['close'])
                hi = float(match['high'])
                lo = float(match['low'])
                inner_lower = float(match['inner_lower'])
                pos.high_watermark = max(pos.high_watermark, hi)

                if minute >= FORCE_FLAT_MINUTE:
                    t = close_trade(pos, 'SESSION_FORCE_FLAT', p, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

                if lo <= pos.stop:
                    t = close_trade(pos, 'V22_ADAPTIVE_HARD_STOP', pos.stop, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

                if not pos.partial_done and hi >= tp1:
                    pos.realized_pct += partial_fraction * (tp1 / pos.entry_price - 1.0)
                    pos.remaining_fraction = 1.0 - partial_fraction
                    pos.partial_done = True

                if policy.candle_fully_below_inner_lower(hi, inner_lower):
                    reason = (
                        'V22_RUNNER_FULL_CANDLE_BELOW_INNER_LOWER'
                        if pos.partial_done
                        else 'V22_PRE_TP1_FULL_CANDLE_BELOW_INNER_LOWER'
                    )
                    t = close_trade(pos, reason, p, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = []
            for r in base_candidates:
                if float(r['score']) < min_score:
                    continue
                if block_falling and str(r['regime']) == 'FALLING':
                    continue
                candidates.append(r)
            if candidates:
                r = max(candidates, key=lambda x: float(x['score']))
                sym = str(r['symbol'])
                price = float(r['close'])
                entry_inner_lower = float(r['inner_lower'])
                risk_pct = policy.structural_risk_pct(price, entry_inner_lower)
                stop = policy.initial_stop(price, entry_inner_lower)
                tp1 = policy.tp1_price(price, entry_inner_lower)
                pos = Pos(
                    sym, ts, price, stop,
                    float(r['score']), str(r['stage']), str(r['regime']), price,
                )

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        t = close_trade(pos, 'END_OF_DATA', float(r['close']), r['time'])
        t['risk_pct'] = risk_pct * 100.0
        t['tp1_price'] = tp1
        trades.append(t)

    return pd.DataFrame(trades)


def regime_stats(trades):
    if trades.empty:
        return 0, 0.0
    g = trades.groupby('regime')['pnl_pct'].sum()
    return int((g > 0).sum()), float(g.min())


def robust_score(s, profitable_regimes: int, worst_regime_gross: float) -> float:
    """Heuristic ranking only; raw metrics remain authoritative.

    Rewards positive expectancy/PF and cross-regime stability. It is intentionally
    modest so one spectacular gross result cannot completely hide a weak regime.
    """
    pf = min(float(s['pf']), 3.0)
    return round(
        float(s['gross_pct'])
        + 12.0 * (pf - 1.0)
        + 30.0 * float(s['avg_pct'])
        + 0.02 * float(s['win_rate'])
        + 0.75 * profitable_regimes
        + 0.20 * min(0.0, worst_regime_gross)
        - 0.50 * abs(float(s['max_loss_pct'])),
        4,
    )


def quick_grid():
    # Focused neighborhood around the first positive BASE-entry + V2.2-exit result.
    return itertools.product(
        [False, True],       # block FALLING
        [65.0, 70.0],       # minimum score floor
        [0.008],             # V2.2 minimum structural risk
        [0.020],             # V2.2 maximum structural risk
        [1.75, 2.0, 2.25],  # TP1 R multiple
        [0.50],              # TP1 fraction
    )


def deep_grid():
    return itertools.product(
        [False, True],
        [65.0, 70.0, 75.0],
        [0.006, 0.008, 0.010],
        [0.015, 0.020],
        [1.5, 1.75, 2.0, 2.25, 2.5],
        [0.40, 0.50],
    )


def parse_args():
    p = argparse.ArgumentParser(description='Automated cached DBB parameter sweep')
    p.add_argument('--mode', choices=['quick', 'deep'], default='quick')
    p.add_argument('--workers', type=int, default=2, help='workers only for missing diagnostics cache')
    p.add_argument('--top', type=int, default=10)
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.perf_counter()
    raw = load_data()
    print(f'KR symbols={len(raw)} bars={sum(len(x) for x in raw.values())}', flush=True)

    frames = build_frames_cached(raw, workers=args.workers, rebuild=False)
    print(f'[TIMING] cache_ready={time.perf_counter() - t0:.2f}s', flush=True)

    e0 = time.perf_counter()
    events = build_fast_events(frames)
    print(f'[TIMING] fast_events={len(events)} build={time.perf_counter() - e0:.2f}s', flush=True)

    combos = list(quick_grid() if args.mode == 'quick' else deep_grid())
    print(f'[SWEEP] mode={args.mode} combinations={len(combos)}', flush=True)

    leaderboard = []
    best_trades = None
    best_score = float('-inf')

    for i, combo in enumerate(combos, 1):
        block_falling, min_score, min_risk, max_risk, tp1_r, partial = combo
        c0 = time.perf_counter()
        trades = simulate(
            events, frames,
            block_falling=block_falling,
            min_score=min_score,
            min_risk_pct=min_risk,
            max_risk_pct=max_risk,
            tp1_r_multiple=tp1_r,
            partial_fraction=partial,
        )
        name = f'AUTO_{i:03d}'
        s = summary(name, trades)
        profitable_regimes, worst_regime = regime_stats(trades)
        s.update({
            'block_falling': block_falling,
            'min_score': min_score,
            'min_risk_pct': min_risk * 100.0,
            'max_risk_pct_cfg': max_risk * 100.0,
            'tp1_r': tp1_r,
            'partial_fraction': partial,
            'profitable_regimes': profitable_regimes,
            'worst_regime_gross': round(worst_regime, 4),
        })
        s['robust_score'] = robust_score(s, profitable_regimes, worst_regime)
        leaderboard.append(s)

        if s['robust_score'] > best_score:
            best_score = s['robust_score']
            best_trades = trades.copy()

        print(
            f"[{i:03d}/{len(combos)}] fall_block={block_falling} score>={min_score:.0f} "
            f"risk={min_risk*100:.1f}-{max_risk*100:.1f}% tp1={tp1_r:.2f}R part={partial:.2f} "
            f"trades={s['trades']} win={s['win_rate']:.2f}% gross={s['gross_pct']:+.4f}% "
            f"pf={s['pf']:.3f} robust={s['robust_score']:+.4f} sec={time.perf_counter()-c0:.2f}",
            flush=True,
        )

    board = pd.DataFrame(leaderboard).sort_values(
        ['robust_score', 'pf', 'gross_pct'], ascending=[False, False, False]
    ).reset_index(drop=True)
    board.insert(0, 'rank', range(1, len(board) + 1))

    out = OUT_DIR / f'dbb_auto_sweep_{args.mode}.csv'
    board.to_csv(out, index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT_DIR / f'dbb_auto_sweep_{args.mode}_best_trades.csv', index=False)

    show_cols = [
        'rank', 'block_falling', 'min_score', 'min_risk_pct', 'max_risk_pct_cfg',
        'tp1_r', 'partial_fraction', 'trades', 'win_rate', 'avg_pct', 'gross_pct',
        'pf', 'max_loss_pct', 'profitable_regimes', 'worst_regime_gross', 'robust_score',
    ]
    print('\n=== TOP CANDIDATES ===')
    print(board[show_cols].head(args.top).to_string(index=False))
    print(f'\n[TIMING] total={time.perf_counter() - t0:.2f}s')
    print(f'CSV saved: {out}')


if __name__ == '__main__':
    main()
