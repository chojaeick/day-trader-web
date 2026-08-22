#!/usr/bin/env python3
"""
Prepare temporal OOS sessions for TREND validation.

Uses existing server tools:
  tools/historical_minute_downloader.py SYMBOL YYYYMMDD
  tools/fast_replay_cache_v4.py SYMBOL YYYYMMDD

OOS starts strictly after the last in-sample date 20260721.

The date set is predeclared and spread across Jul/Aug 2026.
Failures are logged and do not stop the whole batch.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/ubuntu/day-trader-api")
PY = ROOT / "venv/bin/python"
DOWN = ROOT / "tools/historical_minute_downloader.py"
CACHE = ROOT / "tools/fast_replay_cache_v4.py"

SYMBOLS = ["AMD", "ARM", "AVGO", "INTC", "NVDA", "SMCI", "TSM"]

DATES = [
    "20260722",
    "20260724",
    "20260728",
    "20260731",
    "20260804",
    "20260806",
    "20260810",
    "20260812",
    "20260814",
    "20260818",
    "20260820",
    "20260821",
]

def run(cmd):
    p = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout

def main():
    print("===== TREND V5.5 OOS PREP =====")
    print("ROOT", ROOT)
    print("SYMBOLS", ",".join(SYMBOLS))
    print("DATES", ",".join(DATES))
    print("TARGET_CASES", len(SYMBOLS) * len(DATES))
    print()

    dl_ok = 0
    dl_fail = []
    cache_ok = 0
    cache_fail = []

    for date in DATES:
        for symbol in SYMBOLS:
            case = f"{symbol}_{date}"
            print(f"===== DOWNLOAD {case} =====", flush=True)

            rc, out = run([PY, DOWN, symbol, date])
            print(out.rstrip())

            if rc == 0:
                dl_ok += 1
            else:
                dl_fail.append(case)
                print("DOWNLOAD_FAILED", case)
                print()
                continue

            print(f"===== CACHE {case} =====", flush=True)
            rc, out = run([PY, CACHE, symbol, date])
            print(out.rstrip())

            if rc == 0:
                cache_ok += 1
            else:
                cache_fail.append(case)
                print("CACHE_FAILED", case)

            print()

    print("===== PREP SUMMARY =====")
    print("DOWNLOAD_OK", dl_ok)
    print("DOWNLOAD_FAIL", len(dl_fail))
    print("CACHE_OK", cache_ok)
    print("CACHE_FAIL", len(cache_fail))

    if dl_fail:
        print("DOWNLOAD_FAILED_CASES", ",".join(dl_fail))
    if cache_fail:
        print("CACHE_FAILED_CASES", ",".join(cache_fail))

    cache_dir = Path("/tmp/fast_replay_cache")
    existing = sorted(cache_dir.glob("*.csv")) if cache_dir.exists() else []
    oos = [
        p for p in existing
        if len(p.stem.split("_", 1)) == 2
        and p.stem.split("_", 1)[1] >= "20260722"
    ]

    print("OOS_CACHE_FILES", len(oos))
    for p in oos:
        print(p.name)

if __name__ == "__main__":
    main()
