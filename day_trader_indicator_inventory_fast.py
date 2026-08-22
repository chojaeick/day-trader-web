#!/usr/bin/env python3
"""
DAY TRADER V4 - FAST INDICATOR / STRATEGY ASSET INVENTORY

Fast version:
- NO simulation
- NO download
- scans only relevant source/back-up files
- skips large logs, CSVs, DBs, cache files
"""

from pathlib import Path
import os
import re
import pandas as pd

ROOT = Path("/home/ubuntu/day-trader-api")
CACHE = Path("/tmp/fast_replay_cache")
OUT = Path("/tmp/day_trader_indicator_inventory.txt")

MAX_FILE_BYTES = 2_000_000

SEARCH_TERMS = [
    ("DYNAMIC_RSI", re.compile(r"dynamic[_\s-]*rsi|rsi[_\s-]*dynamic", re.I)),
    ("MACD", re.compile(r"\bmacd\b", re.I)),
    ("RSI", re.compile(r"\brsi\b", re.I)),
    ("STAGED_10_20_70", re.compile(
        r"(0\.1.{0,100}0\.2.{0,100}0\.7|10\s*%.{0,100}20\s*%.{0,100}70\s*%|"
        r"probe.{0,120}confirm.{0,120}full)", re.I
    )),
]

SKIP_DIRS = {
    ".git", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "dist", "build"
}

SOURCE_EXTS = {".py", ".bak", ".sh", ".md"}

NAME_HINTS = (
    "engine", "trend", "rebound", "scalp", "replay",
    "signal", "entry", "exit", "indicator", "strategy",
    "v4", "v5", "v6", "v7", "rsi", "macd"
)


def source_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in SOURCE_EXTS:
                continue
            if not any(h in fn.lower() for h in NAME_HINTS):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(p)
    return sorted(out)


def main():
    lines = []
    add = lines.append

    add("===== DAY TRADER FAST INDICATOR INVENTORY =====")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("")

    cache_files = sorted(CACHE.glob("*.csv"))
    add("===== 1. CACHE =====")
    add(f"CACHE_FILES {len(cache_files)}")

    all_cols = set()
    sampled = 0
    for p in cache_files[:20]:
        try:
            x = pd.read_csv(p, nrows=1)
            all_cols.update(x.columns)
            sampled += 1
        except Exception:
            pass

    add(f"CACHE_SCHEMAS_SAMPLED {sampled}")
    add("COLUMNS " + ",".join(sorted(all_cols)))
    add("")

    keys = ["macd","rsi","dynamic","mfi","vo","ema","vwap","participation","rp","atr","volume"]
    add("===== 2. INDICATOR COLUMNS =====")
    for key in keys:
        cols = sorted(c for c in all_cols if key in c.lower())
        add(f"{key.upper()}: {','.join(cols) if cols else 'NONE'}")
    add("")

    files = source_files()
    add("===== 3. SOURCE FILES SCANNED =====")
    add(f"COUNT {len(files)}")
    add("")

    counts = {name: 0 for name, _ in SEARCH_TERMS}
    evidence = {name: [] for name, _ in SEARCH_TERMS}

    for p in files:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue

        for i, line in enumerate(text.splitlines(), 1):
            for name, pat in SEARCH_TERMS:
                if pat.search(line):
                    counts[name] += 1
                    if len(evidence[name]) < 80:
                        evidence[name].append(
                            f"{p.relative_to(ROOT)}:{i}: {line.strip()[:220]}"
                        )

    add("===== 4. SOURCE EVIDENCE =====")
    for name in ["DYNAMIC_RSI","MACD","RSI","STAGED_10_20_70"]:
        add(f"[{name}] HITS={counts[name]}")
        if evidence[name]:
            add("\n".join(evidence[name]))
        else:
            add("NONE")
        add("")

    add("===== 5. READINESS =====")
    macd_cols = [c for c in all_cols if "macd" in c.lower()]
    rsi_cols = [c for c in all_cols if "rsi" in c.lower()]
    dyn_cols = [c for c in all_cols if "dynamic" in c.lower()]

    add(f"MACD_IN_CACHE {bool(macd_cols)}")
    add(f"RSI_IN_CACHE {bool(rsi_cols)}")
    add(f"DYNAMIC_RSI_IN_CACHE {bool(dyn_cols)}")
    add(f"MACD_SOURCE_EVIDENCE {counts['MACD'] > 0}")
    add(f"DYNAMIC_RSI_SOURCE_EVIDENCE {counts['DYNAMIC_RSI'] > 0}")
    add(f"STAGED_10_20_70_SOURCE_EVIDENCE {counts['STAGED_10_20_70'] > 0}")

    if macd_cols and (rsi_cols or dyn_cols):
        add("NEXT reuse existing cache features directly.")
    else:
        add("NEXT derive missing MACD/Dynamic-RSI from existing cached OHLCV only; NO DOWNLOAD.")

    OUT.write_text("\n".join(lines), encoding="utf-8")

    print("REPORT", OUT)
    print("SOURCE_FILES_SCANNED", len(files))
    print("CACHE_FILES", len(cache_files))
    print("DONE")


if __name__ == "__main__":
    main()
