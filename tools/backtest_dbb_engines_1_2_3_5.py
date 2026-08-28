"""Fast integrated validation runner for DBB engines 1, 2, 3 and 5.

Critical rule: Engines 1/2/3 MUST reuse the persistent diagnostics cache unless
an explicit cache rebuild is requested elsewhere. Their diagnostics are expensive
and are already fingerprinted against the exact historical bar content by
`build_frames_cached()`.

Engine 5 is the active tuning target. Its current logic is evaluated on the same
historical source after the fixed cached 1/2/3 reference is printed.

Engine 5's 80%+ win rate is a tuning objective only, never a filter.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pandas as pd

from tools.backtest_dbb_kr_v2_v21_v22 import load_data, simulate_legacy, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import (
    CACHE_DIR,
    build_frames_cached,
    simulate_v22_adaptive,
)
from tools.backtest_dbb_engine5_tuner import main as run_engine5

RUNTIME = Path('/home/ubuntu/day-trader-api')
DB = RUNTIME / 'daytrader.db'


def _mem_available_mb() -> float | None:
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return float(line.split()[1]) / 1024.0
    except Exception:
        pass
    return None


def _cache_stats() -> tuple[int, float]:
    files = list(CACHE_DIR.glob('*.pkl')) if CACHE_DIR.exists() else []
    total = sum(p.stat().st_size for p in files if p.exists())
    return len(files), total / (1024.0 * 1024.0)


def print_server_preflight() -> None:
    cpu = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
        loads = f'{load1:.2f}/{load5:.2f}/{load15:.2f}'
    except Exception:
        loads = 'n/a'
    mem = _mem_available_mb()
    disk = shutil.disk_usage(RUNTIME)
    cache_files, cache_mb = _cache_stats()
    db_mb = DB.stat().st_size / (1024.0 * 1024.0) if DB.exists() else 0.0

    print('\n=== SERVER / CACHE PREFLIGHT ===')
    print(f'cpu_count={cpu} loadavg_1m/5m/15m={loads}')
    print(f'mem_available_mb={mem:.1f}' if mem is not None else 'mem_available_mb=n/a')
    print(f'disk_free_gb={disk.free / (1024**3):.2f} db_size_mb={db_mb:.1f}')
    print(f'diagnostics_cache_dir={CACHE_DIR}')
    print(f'diagnostics_cache_files={cache_files} cache_size_mb={cache_mb:.1f}')
    print('[CACHE RULE] Engines 1/2/3 use fingerprinted persistent cache; no blind recompute.')


def run_engines_123_cached(raw):
    t0 = time.perf_counter()
    frames = build_frames_cached(raw, workers=2, rebuild=False)
    cache_ready = time.perf_counter() - t0

    s0 = time.perf_counter()
    base = simulate_legacy(frames, 'base_entry')
    struct = simulate_legacy(frames, 'structure_entry')
    v22 = simulate_v22_adaptive(frames, 'structure_entry')
    sim_sec = time.perf_counter() - s0

    table = pd.DataFrame([
        summary('ENGINE_1_V2_BASE_1M', base),
        summary('ENGINE_2_V21_STRUCTURE_1M', struct),
        summary('ENGINE_3_V22_ADAPTIVE_1M', v22),
    ])

    print('\n=== PHASE A — CACHED FIXED REFERENCE: ENGINES 1 / 2 / 3 ===')
    print(table.to_string(index=False))
    print(f'[TIMING] cache_ready={cache_ready:.2f}s simulations_123={sim_sec:.2f}s')
    return table


def main():
    total0 = time.perf_counter()
    print_server_preflight()

    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)

    run_engines_123_cached(raw)

    print('\n' + '=' * 88)
    print('PHASE B — ENGINE 5 CLARIFIED LOGIC + TUNING')
    print('Compare every Engine 5 candidate against the cached Engine 1/2/3 reference above.')
    print('=' * 88, flush=True)
    run_engine5()

    print('\n' + '=' * 88)
    print('VALIDATION RULE')
    print('Do not choose Engine 5 by win rate alone: compare trades, win rate, avg_pct, gross_pct, PF, max loss, first-TP rate and scale-out behavior against Engines 1/2/3.')
    print(f'[TIMING] integrated_total={time.perf_counter() - total0:.2f}s')
    print('=' * 88)


if __name__ == '__main__':
    main()
