#!/usr/bin/env python3
"""
DAY TRADER V4 - DRSI/MACD SOURCE RECOVERY

Purpose:
- Recover exact historical staged-entry logic and any real MACD / Dynamic-RSI code.
- Excludes archaeology/inventory helper scripts to avoid false positives.
- NO simulation, NO download.
"""

from pathlib import Path
import os
import re

ROOT = Path("/home/ubuntu/day-trader-api")
OUT = Path("/tmp/day_trader_drsi_macd_source_recovery.txt")

SKIP_DIRS = {".git","venv","__pycache__","node_modules",".pytest_cache","dist","build"}
SKIP_NAMES = {
    "day_trader_engine_archaeology.py",
    "day_trader_engine_registry_extract.py",
    "day_trader_indicator_inventory.py",
    "day_trader_indicator_inventory_fast.py",
    "day_trader_drsi_macd_source_recovery.py",
}

MAX_BYTES = 2_000_000
EXTS = {".py",".bak",".sh",".md",".txt"}

PATTERNS = {
    "DYNAMIC_RSI": re.compile(r"dynamic[_\s-]*rsi|rsi[_\s-]*dynamic", re.I),
    "MACD": re.compile(r"\bmacd\b", re.I),
    "RSI": re.compile(r"\brsi\b", re.I),
    "STAGED": re.compile(
        r"probe|confirm|pyramid|full|10\s*%|20\s*%|70\s*%|0\.1|0\.2|0\.7",
        re.I,
    ),
}

def iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_NAMES:
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in EXTS:
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield p

def context(lines, idx, radius=3):
    a = max(0, idx-radius)
    b = min(len(lines), idx+radius+1)
    return [(j+1, lines[j]) for j in range(a,b)]

def main():
    out=[]
    add=out.append

    add("===== DRSI / MACD SOURCE RECOVERY =====")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("")

    # 1. Exact historical staged simulator
    p = ROOT / "tools/trend_pyramid_sim_v1.py"
    add("===== 1. trend_pyramid_sim_v1.py =====")
    if p.exists():
        txt = p.read_text(errors="ignore")
        add(txt[:30000])
    else:
        add("MISSING")
    add("")

    # 2. Real source evidence excluding helper scripts
    evidence={k:[] for k in PATTERNS}
    scanned=0

    for p in iter_files():
        scanned += 1
        try:
            lines=p.read_text(errors="ignore").splitlines()
        except Exception:
            continue

        for i,line in enumerate(lines):
            for name,pat in PATTERNS.items():
                if pat.search(line):
                    rel=p.relative_to(ROOT)
                    block=context(lines,i,2)
                    evidence[name].append((rel,i+1,block))

    add("===== 2. REAL SOURCE EVIDENCE =====")
    add(f"FILES_SCANNED {scanned}")
    add("")

    for name in ["DYNAMIC_RSI","MACD","RSI","STAGED"]:
        add(f"### {name} HITS {len(evidence[name])}")
        for rel,ln,block in evidence[name][:100]:
            add(f"--- {rel}:{ln} ---")
            for n,s in block:
                add(f"{n}: {s[:240]}")
        if not evidence[name]:
            add("NONE")
        add("")

    # 3. Exact candidate filenames
    add("===== 3. CANDIDATE FILES =====")
    names=[]
    for p in iter_files():
        n=p.name.lower()
        if any(x in n for x in ["pyramid","rsi","macd","trend","scalp","v7","rebound"]):
            names.append(str(p.relative_to(ROOT)))
    for n in sorted(set(names))[:300]:
        add(n)

    add("")
    add("===== 4. CONCLUSION FLAGS =====")
    add(f"REAL_DYNAMIC_RSI_IMPL_FOUND {len(evidence['DYNAMIC_RSI']) > 0}")
    add(f"REAL_MACD_IMPL_FOUND {len(evidence['MACD']) > 0}")
    add(f"REAL_RSI_IMPL_FOUND {len(evidence['RSI']) > 0}")
    add(f"STAGED_LOGIC_FOUND {len(evidence['STAGED']) > 0}")
    add("")
    add("NEXT:")
    add("- If historical DRSI/MACD implementation exists: reproduce it exactly.")
    add("- If not: define the DRSI formula explicitly before baseline testing.")
    add("- Use existing cache OHLCV only; do not download data.")

    OUT.write_text("\n".join(out), encoding="utf-8")
    print("REPORT", OUT)
    print("FILES_SCANNED", scanned)
    print("DONE")

if __name__ == "__main__":
    main()
