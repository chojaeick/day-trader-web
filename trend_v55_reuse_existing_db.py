#!/usr/bin/env python3
"""
TREND V5.5 REUSE EXISTING DB ONLY

No downloading.

1) Scan historical_minute_bars for the frozen TREND universe.
2) Use only dates >= 20260722.
3) If cache already exists, skip it.
4) Otherwise run fast_replay_cache_v4.py using the data already in DB.
5) Print compact summary only.

This script NEVER calls historical_minute_downloader.py.
"""

import sqlite3
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu/day-trader-api")
DB = ROOT / "daytrader.db"
PY = ROOT / "venv/bin/python"
CACHE_TOOL = ROOT / "tools/fast_replay_cache_v4.py"
CACHE_DIR = Path("/tmp/fast_replay_cache")

SYMBOLS = ["AMD", "ARM", "AVGO", "INTC", "NVDA", "SMCI", "TSM"]
MIN_DATE = "20260722"
MIN_REGULAR_ROWS = 300


def run_cache(symbol, date):
    p = subprocess.run(
        [str(PY), str(CACHE_TOOL), symbol, date],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB)
    cur = con.cursor()

    q = """
    SELECT
        symbol,
        trade_date,
        COUNT(*) AS n,
        SUM(CASE WHEN session='REGULAR' THEN 1 ELSE 0 END) AS regular_n
    FROM historical_minute_bars
    WHERE interval_min=1
      AND trade_date >= ?
      AND symbol IN ({})
    GROUP BY symbol, trade_date
    ORDER BY trade_date, symbol
    """.format(",".join("?" for _ in SYMBOLS))

    rows = list(cur.execute(q, [MIN_DATE] + SYMBOLS))
    con.close()

    usable = [
        (symbol, date, n, regular_n or 0)
        for symbol, date, n, regular_n in rows
        if (regular_n or 0) >= MIN_REGULAR_ROWS
    ]

    print("===== TREND V5.5 EXISTING-DB REUSE =====")
    print("DB_CASES_FOUND", len(rows))
    print("USABLE_CASES", len(usable))
    print("MIN_DATE", MIN_DATE)
    print("NO_DOWNLOAD True")
    print()

    skipped = 0
    built = 0
    failed = []

    total = len(usable)

    for idx, (symbol, date, n, regular_n) in enumerate(usable, 1):
        case = f"{symbol}_{date}"
        cp = CACHE_DIR / f"{case}.csv"

        if cp.exists() and cp.stat().st_size > 0:
            skipped += 1
            print(f"[{idx:03d}/{total:03d}] SKIP {case}", flush=True)
            continue

        rc, out = run_cache(symbol, date)

        if rc == 0 and cp.exists() and cp.stat().st_size > 0:
            built += 1
            print(f"[{idx:03d}/{total:03d}] BUILT {case}", flush=True)
        else:
            last = out.strip().splitlines()[-1] if out.strip() else "unknown"
            failed.append((case, last))
            print(f"[{idx:03d}/{total:03d}] FAIL {case}", flush=True)

    available = sorted(
        p.name
        for p in CACHE_DIR.glob("*.csv")
        if "_" in p.stem
        and p.stem.split("_", 1)[0] in SYMBOLS
        and p.stem.split("_", 1)[1] >= MIN_DATE
    )

    dates = sorted({
        Path(x).stem.split("_", 1)[1]
        for x in available
    })

    print()
    print("===== SUMMARY =====")
    print("USABLE_DB_CASES", total)
    print("SKIPPED_EXISTING_CACHE", skipped)
    print("BUILT_FROM_EXISTING_DB", built)
    print("FAILED_CACHE_BUILD", len(failed))
    print("AVAILABLE_OOS_CACHE", len(available))
    print("AVAILABLE_OOS_DATES", len(dates))
    print("DATES", ",".join(dates))

    if failed:
        print()
        print("FAILED_CASES")
        for case, msg in failed:
            print(case, msg)

    print()
    print("READY_FOR_VALIDATION", len(available) >= 25 and len(dates) >= 6)


if __name__ == "__main__":
    main()
