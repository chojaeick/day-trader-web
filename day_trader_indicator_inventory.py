#!/usr/bin/env python3
"""
DAY TRADER V4 - INDICATOR / STRATEGY ASSET INVENTORY

No simulation. No downloads.

Checks:
1. columns available in fast replay cache
2. whether MACD / RSI / Dynamic RSI features already exist
3. source files containing MACD / RSI / staged 10/20/70 logic
4. reports exact evidence paths/lines
"""

from pathlib import Path
import pandas as pd
import re

ROOT = Path("/home/ubuntu/day-trader-api")
CACHE = Path("/tmp/fast_replay_cache")
OUT = Path("/tmp/day_trader_indicator_inventory.txt")

SEARCH_TERMS = [
    ("DYNAMIC_RSI", re.compile(r"dynamic[_\s-]*rsi|rsi[_\s-]*dynamic", re.I)),
    ("MACD", re.compile(r"\bmacd\b", re.I)),
    ("RSI", re.compile(r"\brsi\b", re.I)),
    ("STAGED_10_20_70", re.compile(
        r"(0\.1.{0,80}0\.2.{0,80}0\.7|10\s*%.{0,80}20\s*%.{0,80}70\s*%|"
        r"probe.{0,100}confirm.{0,100}full)", re.I
    )),
]

def main():
    out = []
    add = out.append

    add("===== DAY TRADER INDICATOR / STRATEGY ASSET INVENTORY =====")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("")

    files = sorted(CACHE.glob("*.csv"))
    add("===== 1. CACHE INVENTORY =====")
    add(f"CACHE_FILES {len(files)}")

    all_cols = set()
    samples = []
    for p in files[:30]:
        try:
            x = pd.read_csv(p, nrows=3)
            cols = list(x.columns)
            all_cols.update(cols)
            samples.append((p.name, cols))
        except Exception:
            pass

    add("UNION_COLUMNS")
    add(",".join(sorted(all_cols)))
    add("")

    indicator_cols = {}
    for key in ["macd","rsi","dynamic","mfi","vo","ema","vwap","participation","rp","atr","volume"]:
        indicator_cols[key] = sorted(c for c in all_cols if key in c.lower())

    add("===== 2. INDICATOR COLUMNS FOUND =====")
    for key, cols in indicator_cols.items():
        add(f"{key.upper()}: {','.join(cols) if cols else 'NONE'}")
    add("")

    add("===== 3. SAMPLE CACHE SCHEMAS =====")
    for name, cols in samples[:8]:
        add(f"{name}: {','.join(cols)}")
    add("")

    add("===== 4. SOURCE-CODE EVIDENCE =====")
    candidates = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if "venv" in p.parts or ".git" in p.parts or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() not in {".py",".sh",".md",".txt",".bak"}:
            continue
        candidates.append(p)

    counts = {name:0 for name,_ in SEARCH_TERMS}
    for p in candidates:
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except Exception:
            continue

        file_hits = []
        for i,line in enumerate(lines,1):
            for name,pat in SEARCH_TERMS:
                if pat.search(line):
                    counts[name] += 1
                    file_hits.append((name,i,line.strip()[:240]))
        if file_hits:
            add(f"--- {p} ---")
            for name,i,line in file_hits[:40]:
                add(f"{name} L{i}: {line}")

    add("")
    add("===== 5. HIT COUNTS =====")
    for name in counts:
        add(f"{name} {counts[name]}")

    add("")
    add("===== 6. READINESS =====")
    macd_cache = bool(indicator_cols["macd"])
    rsi_cache = bool(indicator_cols["rsi"])
    dyn_cache = bool(indicator_cols["dynamic"])

    add(f"MACD_IN_CACHE {macd_cache}")
    add(f"RSI_IN_CACHE {rsi_cache}")
    add(f"DYNAMIC_RSI_IN_CACHE {dyn_cache}")
    add(f"MACD_SOURCE_EVIDENCE {counts['MACD'] > 0}")
    add(f"DYNAMIC_RSI_SOURCE_EVIDENCE {counts['DYNAMIC_RSI'] > 0}")
    add(f"STAGED_10_20_70_SOURCE_EVIDENCE {counts['STAGED_10_20_70'] > 0}")

    if macd_cache and (rsi_cache or dyn_cache):
        add("NEXT: reuse cache features directly for DRSI+MACD baseline.")
    else:
        add("NEXT: derive missing indicators from existing cached OHLCV only; NO DOWNLOAD.")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print("REPORT", OUT)
    print("CACHE_FILES", len(files))
    print("DONE")

if __name__ == "__main__":
    main()
