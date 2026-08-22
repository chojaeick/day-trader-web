#!/usr/bin/env python3
"""
DAY TRADER V4 — MONDAY PROTOTYPE WIRING AUDIT V1

Purpose:
Inspect existing live KR prototype assets before modifying anything.

NO DATA DOWNLOAD
NO STRATEGY SIMULATION
NO SERVICE RESTART
NO FILE MODIFICATION

Checks:
- KR live/shadow services
- live_server API / engine files
- KR trend/orderflow runners
- Kakao reporter/alert code
- web-app files and KR UI references
- endpoints/signals related to BUY/ADD/HOLD/STOP/TAKE PROFIT
"""

from pathlib import Path
import subprocess, os, re

ROOT = Path("/home/ubuntu/day-trader-api")
REPO = Path("/home/ubuntu/day-trader-api-repo")
OUT = Path("/tmp/day_trader_prototype_wiring_audit.txt")

def run(cmd):
    try:
        return subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=20
        ).stdout.strip()
    except Exception as e:
        return f"<ERROR {e}>"

def grep_files(base, patterns, exts=(".py",".js",".jsx",".ts",".tsx",".html",".md")):
    hits=[]
    pats=[re.compile(p,re.I) for p in patterns]
    if not base.exists():
        return hits
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in {".git","venv","node_modules","__pycache__"}]
        for fn in filenames:
            if not fn.endswith(exts):
                continue
            p=Path(dirpath)/fn
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                lines=p.read_text(errors="ignore").splitlines()
            except Exception:
                continue
            for i,line in enumerate(lines,1):
                if any(pt.search(line) for pt in pats):
                    hits.append((str(p.relative_to(base)),i,line.strip()[:220]))
                    if len(hits)>=250:
                        return hits
    return hits

def main():
    out=[]
    add=out.append

    add("===== DAY TRADER V4 MONDAY PROTOTYPE WIRING AUDIT =====")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("NO_RESTART True")
    add("")

    add("===== 1. SERVICES =====")
    svc = run(["bash","-lc",
        "systemctl list-units --type=service --all | "
        "grep -Ei 'day-trader|kr-trend|kr-orderflow' | head -80"])
    add(svc or "NONE")
    add("")

    add("===== 2. CORE FILES =====")
    candidates = [
        ROOT/"live_server/v4_engine.py",
        ROOT/"live_server/main.py",
        ROOT/"live_server/kiwoom.py",
        ROOT/"live_server/config.py",
    ]
    for c in candidates:
        add(f"{c}: {'EXISTS' if c.exists() else 'MISSING'}")
    add("")

    add("===== 3. KR LIVE / SHADOW FILES =====")
    krhits=grep_files(ROOT,[
        r"kr[_-]?trend", r"kr[_-]?orderflow", r"ka10080",
        r"KOREA", r"market.?=.?'KR'", r"market.?==.?'KR'"
    ])
    for f,i,line in krhits[:120]:
        add(f"{f}:{i}: {line}")
    if not krhits: add("NONE")
    add("")

    add("===== 4. SIGNAL ACTION VOCABULARY =====")
    sighits=grep_files(ROOT,[
        r"\bBUY\b", r"\bADD\b", r"\bHOLD\b", r"\bSTOP\b",
        r"TAKE.?PROFIT", r"ENTRY", r"EXIT", r"position_gate"
    ])
    for f,i,line in sighits[:120]:
        add(f"{f}:{i}: {line}")
    if not sighits: add("NONE")
    add("")

    add("===== 5. KAKAO / ALERT WIRING =====")
    khits=grep_files(ROOT,[
        r"kakao", r"live.?alert", r"shadow.?alert", r"reporter"
    ])
    for f,i,line in khits[:100]:
        add(f"{f}:{i}: {line}")
    if not khits: add("NONE")
    add("")

    add("===== 6. WEB APP / UI WIRING =====")
    whits=grep_files(REPO,[
        r"Trading", r"Briefing", r"Validation", r"Archive",
        r"KOREA", r"KR", r"Finder", r"Tracker",
        r"Signal Quality", r"Position Intelligence"
    ])
    for f,i,line in whits[:140]:
        add(f"{f}:{i}: {line}")
    if not whits: add("NONE")
    add("")

    add("===== 7. PROTOTYPE READINESS FLAGS =====")
    add(f"CORE_ENGINE_EXISTS {(ROOT/'live_server/v4_engine.py').exists()}")
    add(f"KR_CODE_EVIDENCE {bool(krhits)}")
    add(f"SIGNAL_ACTION_EVIDENCE {bool(sighits)}")
    add(f"KAKAO_EVIDENCE {bool(khits)}")
    add(f"WEB_UI_EVIDENCE {bool(whits)}")
    add("")
    add("NEXT:")
    add("1. Do not rebuild app or KR live stack.")
    add("2. Reuse existing KR Finder/Tracker + shadow services.")
    add("3. Insert prototype engine decision output behind existing signal/action interface.")
    add("4. Surface engine state in KR Trading UI and Kakao.")
    add("5. Keep manual order only for Monday prototype.")

    OUT.write_text("\n".join(out),encoding="utf-8")
    print("REPORT", OUT)
    print("KR_EVIDENCE", bool(krhits))
    print("KAKAO_EVIDENCE", bool(khits))
    print("WEB_UI_EVIDENCE", bool(whits))
    print("DONE")

if __name__=="__main__":
    main()
