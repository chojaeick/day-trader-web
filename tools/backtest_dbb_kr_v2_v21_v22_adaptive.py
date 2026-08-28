from __future__ import annotations

import argparse
import hashlib
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from live_server.double_bollinger_v22 import DoubleBollingerV22ExitPolicy
from tools.backtest_dbb_kr_v2_v21_v22 import (
    FORCE_FLAT_MINUTE,
    NO_ENTRY_MINUTE,
    Pos,
    build_events,
    close_trade,
    enrich,
    load_data,
    print_exit_reasons,
    print_regime,
    simulate_legacy,
    summary,
)

V22_POLICY = DoubleBollingerV22ExitPolicy()
CACHE_VERSION = 'dbb_diag_exact_v1'
CACHE_DIR = Path('/home/ubuntu/day-trader-api/.cache/dbb_diagnostics')


def _bars_fingerprint(symbol: str, bars: pd.DataFrame) -> str:
    """Cheap content fingerprint so cached diagnostics are reused only for identical bars."""
    cols = ['time', 'open', 'high', 'low', 'close', 'volume']
    h = hashlib.sha256()
    h.update(CACHE_VERSION.encode())
    h.update(str(symbol).encode())
    h.update(str(len(bars)).encode())
    hv = pd.util.hash_pandas_object(bars[cols], index=False).values
    h.update(hv.tobytes())
    return h.hexdigest()[:20]


def _cache_path(symbol: str, fingerprint: str) -> Path:
    return CACHE_DIR / f'{symbol}_{fingerprint}.pkl'


def _compute_one(args):
    symbol, bars = args
    t0 = time.perf_counter()
    frame = enrich(symbol, bars)
    return symbol, frame, time.perf_counter() - t0


def build_frames_cached(raw, workers: int | None = None, rebuild: bool = False):
    """Build exact legacy diagnostics once, then persist per-symbol DataFrames.

    The expensive `enrich()` implementation is intentionally unchanged so cached
    results are bit-for-bit compatible with prior V2/V2.1/V2.2 backtests.
    Subsequent strategy tests load the cached diagnostics instead of recomputing
    RSI/MACD/Bollinger diagnostics for every 1-minute bar.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frames = {}
    missing = []

    for sym, bars in sorted(raw.items()):
        fp = _bars_fingerprint(sym, bars)
        path = _cache_path(sym, fp)
        if not rebuild and path.exists():
            t0 = time.perf_counter()
            frames[sym] = pd.read_pickle(path)
            print(
                f'[CACHE HIT] {sym} bars={len(bars)} diag={len(frames[sym])} '
                f'load={time.perf_counter() - t0:.3f}s',
                flush=True,
            )
        else:
            missing.append((sym, bars, path))

    if not missing:
        return frames

    cpu = os.cpu_count() or 1
    if workers is None:
        workers = max(1, min(cpu, len(missing)))
    else:
        workers = max(1, min(int(workers), len(missing)))

    print(
        f'[CACHE BUILD] missing={len(missing)} workers={workers} cpu={cpu} '
        f'cache_dir={CACHE_DIR}',
        flush=True,
    )

    # Keep one writer in the parent process. Workers only calculate diagnostics.
    lookup = {sym: path for sym, _, path in missing}
    if workers == 1:
        results = [_compute_one((sym, bars)) for sym, bars, _ in missing]
        for sym, frame, elapsed in results:
            path = lookup[sym]
            # Remove stale cache files for the same symbol before storing the new one.
            for old in CACHE_DIR.glob(f'{sym}_*.pkl'):
                if old != path:
                    old.unlink(missing_ok=True)
            frame.to_pickle(path)
            frames[sym] = frame
            print(f'[CACHE SAVE] {sym} diag={len(frame)} compute={elapsed:.1f}s', flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(_compute_one, (sym, bars)): sym
                for sym, bars, _ in missing
            }
            done = 0
            for fut in as_completed(futs):
                sym, frame, elapsed = fut.result()
                path = lookup[sym]
                for old in CACHE_DIR.glob(f'{sym}_*.pkl'):
                    if old != path:
                        old.unlink(missing_ok=True)
                frame.to_pickle(path)
                frames[sym] = frame
                done += 1
                print(
                    f'[CACHE SAVE {done}/{len(missing)}] {sym} '
                    f'diag={len(frame)} compute={elapsed:.1f}s',
                    flush=True,
                )

    return {sym: frames[sym] for sym in sorted(frames)}


def simulate_v22_adaptive(frames, entry_col: str = 'structure_entry'):
    """Selected entries + V2.2 adaptive stop / 2R TP1 / full-candle structural exit."""
    events = build_events(frames)
    pos = None
    trades = []
    tp1 = None
    risk_pct = None

    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]

        if pos is not None:
            match = next((r for s, r in rows if s == pos.symbol), None)
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
                    pos.realized_pct += 0.5 * (tp1 / pos.entry_price - 1.0)
                    pos.remaining_fraction = 0.5
                    pos.partial_done = True

                if V22_POLICY.candle_fully_below_inner_lower(hi, inner_lower):
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
            candidates = [(s, r) for s, r in rows if bool(r[entry_col])]
            if candidates:
                sym, r = max(candidates, key=lambda z: float(z[1]['score']))
                price = float(r['close'])
                entry_inner_lower = float(r['inner_lower'])
                risk_pct = V22_POLICY.structural_risk_pct(price, entry_inner_lower)
                stop = V22_POLICY.initial_stop(price, entry_inner_lower)
                tp1 = V22_POLICY.tp1_price(price, entry_inner_lower)
                pos = Pos(
                    sym, ts, price, stop,
                    float(r['score']), str(r['stage']), str(r['regime']), price
                )

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        t = close_trade(pos, 'END_OF_DATA', float(r['close']), r['time'])
        t['risk_pct'] = risk_pct * 100.0
        t['tp1_price'] = tp1
        trades.append(t)

    return pd.DataFrame(trades)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--workers', type=int, default=None, help='parallel workers for first diagnostics cache build')
    p.add_argument('--rebuild-cache', action='store_true', help='ignore and rebuild diagnostics cache')
    p.add_argument('--hybrid', action='store_true', help='also test V2 BASE entry + V2.2 adaptive exit')
    return p.parse_args()


def main():
    args = parse_args()
    total_t0 = time.perf_counter()
    raw = load_data()
    print('KR symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()))

    diag_t0 = time.perf_counter()
    frames = build_frames_cached(raw, workers=args.workers, rebuild=args.rebuild_cache)
    print(f'[TIMING] diagnostics_ready={time.perf_counter() - diag_t0:.2f}s', flush=True)

    sim_t0 = time.perf_counter()
    base = simulate_legacy(frames, 'base_entry')
    struct = simulate_legacy(frames, 'structure_entry')
    v22 = simulate_v22_adaptive(frames, 'structure_entry')
    hybrid = simulate_v22_adaptive(frames, 'base_entry') if args.hybrid else None
    print(f'[TIMING] simulations={time.perf_counter() - sim_t0:.2f}s', flush=True)

    rows = [
        summary('V2_BASE', base),
        summary('V2.1_STRUCTURE', struct),
        summary('V2.2_ADAPTIVE_EXIT', v22),
    ]
    if hybrid is not None:
        rows.append(summary('V2_BASE_ENTRY_V22_EXIT', hybrid))

    print('\n=== SUMMARY ===')
    print(pd.DataFrame(rows).to_string(index=False))

    print_regime('V2_BASE', base)
    print_regime('V2.1_STRUCTURE', struct)
    print_regime('V2.2_ADAPTIVE_EXIT', v22)
    if hybrid is not None:
        print_regime('V2_BASE_ENTRY_V22_EXIT', hybrid)

    print_exit_reasons('V2_BASE', base)
    print_exit_reasons('V2.1_STRUCTURE', struct)
    print_exit_reasons('V2.2_ADAPTIVE_EXIT', v22)
    if hybrid is not None:
        print_exit_reasons('V2_BASE_ENTRY_V22_EXIT', hybrid)

    base.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v2_base_trades_3way.csv', index=False)
    struct.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v21_structure_trades_3way.csv', index=False)
    v22.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v22_adaptive_trades_3way.csv', index=False)
    if hybrid is not None:
        hybrid.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v2base_entry_v22_exit_trades.csv', index=False)

    print(f'[TIMING] total={time.perf_counter() - total_t0:.2f}s')
    print('CSV saved.')


if __name__ == '__main__':
    main()
