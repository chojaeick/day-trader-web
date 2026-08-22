#!/usr/bin/env python3
"""
DAY TRADER V4 - ENGINE ARCHAEOLOGY / REGISTRY RECOVERY

Purpose:
- Recover historical engine lineage from the server.
- Do NOT run any trading simulation.
- Do NOT download any market data.
- Produce one concise report for V1~V7 / SCALP / REBOUND / TREND / CORE.
"""

from pathlib import Path
import subprocess
import re

ROOT = Path("/home/ubuntu/day-trader-api")
REPO = Path("/home/ubuntu/day-trader-api-repo")
OUT = Path("/tmp/day_trader_engine_archaeology.txt")

KEYWORDS = [
    "v1","v2","v3","v4","v5","v6","v7",
    "scalp","rebound","trend","dynamic","rsi","macd",
    "exit","entry","replay","backtest","shadow"
]

EXTS = {".py",".sh",".md",".txt",".json",".bak",".csv"}

def run(cmd, cwd=None):
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=30
        )
        return p.stdout.strip()
    except Exception as e:
        return f"<ERROR {e}>"

def interesting(path):
    n = path.name.lower()
    if any(k in n for k in KEYWORDS):
        return True
    return False

def safe_lines(path, patterns, max_hits=30):
    hits=[]
    try:
        txt = path.read_text(errors="ignore")
    except Exception:
        return hits
    for i,line in enumerate(txt.splitlines(),1):
        low=line.lower()
        if any(p in low for p in patterns):
            s=line.strip()
            if s:
                hits.append((i,s[:220]))
                if len(hits)>=max_hits:
                    break
    return hits

def main():
    lines=[]
    add=lines.append

    add("===== DAY TRADER V4 ENGINE ARCHAEOLOGY =====")
    add(f"ROOT {ROOT}")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("")

    # 1. Relevant files
    files=[]
    for base in [ROOT, REPO]:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if ".git" in p.parts or "venv" in p.parts or "__pycache__" in p.parts:
                continue
            if p.suffix.lower() in EXTS and interesting(p):
                files.append(p)

    # de-dupe by real string path
    uniq=[]
    seen=set()
    for p in sorted(files, key=lambda x: str(x)):
        sp=str(p)
        if sp not in seen:
            seen.add(sp); uniq.append(p)

    add("===== 1. RELEVANT FILES =====")
    add(f"COUNT {len(uniq)}")
    for p in uniq[:300]:
        add(str(p))
    if len(uniq)>300:
        add(f"... {len(uniq)-300} MORE")
    add("")

    # 2. Group by engine family
    fams={"CORE":[],"V7":[],"SCALP":[],"REBOUND":[],"TREND":[],"DYNAMIC_RSI_MACD":[],"REPLAY_OTHER":[]}
    for p in uniq:
        n=p.name.lower()
        if "trend" in n:
            fams["TREND"].append(p)
        if "rebound" in n:
            fams["REBOUND"].append(p)
        if "scalp" in n:
            fams["SCALP"].append(p)
        if re.search(r'(^|[_\-.])v7([_\-.]|$)', n):
            fams["V7"].append(p)
        if ("dynamic" in n and "rsi" in n) or "macd" in n:
            fams["DYNAMIC_RSI_MACD"].append(p)
        if "v4_engine" in n or "live_server" in str(p):
            fams["CORE"].append(p)
        if "replay" in n or "backtest" in n:
            fams["REPLAY_OTHER"].append(p)

    add("===== 2. FAMILY INVENTORY =====")
    for fam, arr in fams.items():
        add(f"[{fam}] {len(arr)}")
        for p in arr[:80]:
            add(f"  {p}")
    add("")

    # 3. Key code evidence
    patterns = [
        "dynamic rsi","dynamic_rsi","macd","rsi",
        "probe","confirm","full","participation",
        "profit_floor","peak50","five_break","vwap",
        "ema9","ema20","rebound","scalp","trend",
        "cost","net","win rate","mfe","mae"
    ]

    add("===== 3. KEY CODE / RESULT EVIDENCE =====")
    candidates=[]
    for fam in ["V7","SCALP","REBOUND","TREND","DYNAMIC_RSI_MACD","CORE","REPLAY_OTHER"]:
        for p in fams[fam]:
            if p not in candidates:
                candidates.append(p)

    for p in candidates[:160]:
        hits=safe_lines(p,patterns,max_hits=16)
        if not hits:
            continue
        add(f"--- {p} ---")
        for ln,s in hits:
            add(f"{ln}: {s}")
    add("")

    # 4. tmp result files
    add("===== 4. /tmp RESULT ARTIFACTS =====")
    tmp=Path("/tmp")
    tps=[]
    if tmp.exists():
        for p in tmp.iterdir():
            if p.is_file():
                n=p.name.lower()
                if any(k in n for k in ["trend","scalp","rebound","replay","v7","backtest"]):
                    tps.append(p)
    for p in sorted(tps, key=lambda x:x.stat().st_mtime, reverse=True)[:120]:
        add(f"{p.name} size={p.stat().st_size}")
        if p.suffix.lower() in {".txt",".log"}:
            try:
                tail="\n".join(p.read_text(errors="ignore").splitlines()[-12:])
                add(tail)
            except Exception:
                pass
    add("")

    # 5. Git recent history
    add("===== 5. GIT RECENT COMMITS =====")
    if REPO.exists():
        add(run(["git","log","--oneline","--decorate","-80"], REPO))
    add("")

    # 6. Services
    add("===== 6. DAY TRADER SERVICES =====")
    svc=run(["bash","-lc","systemctl list-unit-files | grep -Ei 'day-trader|trend|rebound|scalp|orderflow' | head -100"])
    add(svc)
    add("")

    # 7. Existing DB/cache state
    add("===== 7. DATA STATE =====")
    add("historical_minute_bars: existing DB source of truth")
    cache=Path("/tmp/fast_replay_cache")
    cs=list(cache.glob("*.csv")) if cache.exists() else []
    add(f"FAST_REPLAY_CACHE_FILES {len(cs)}")
    add("")

    # 8. Recovery TODO
    add("===== 8. RECOVERY TODO =====")
    add("1. Identify exact V1-V7 lineage and results from evidence above.")
    add("2. Identify SCALP versions and causal results.")
    add("3. Identify REBOUND V4.9.0C variants and results.")
    add("4. Identify any prior Dynamic RSI / MACD implementation.")
    add("5. Update ENGINE_REGISTRY before any new engine simulation.")
    add("6. Next new baseline after registry: Dynamic RSI + MACD 10/20/70.")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("REPORT", OUT)
    print("RELEVANT_FILES", len(uniq))
    print("TMP_ARTIFACTS", len(tps))
    print("DONE")

if __name__ == "__main__":
    main()
