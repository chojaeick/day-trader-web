from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime, timezone

ROOTS = [Path('/tmp'), Path('/home/ubuntu/day-trader-api')]
OUTDIR = Path('/home/ubuntu/day-trader-api') / ('engine_runtime_confirm_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
OUTDIR.mkdir(parents=True, exist_ok=True)
OUT = OUTDIR / 'TREND_RUNTIME_CONFIRMATION.md'

TARGETS = {
    'V51': [r'V5\.1', r'FULL_CONFIRMATION', r'FULL confirmation', r'PART15', r'FLOW_COMBO'],
    'V52': [r'V5\.2', r'PART18', r'minimal participation', r'participation gate'],
    'V53': [r'V5\.3', r'EXIT_CAPTURE', r'PEAK50', r'TIGHT'],
    'V54': [r'V5\.4', r'COST/STRESS', r'COST / STRESS', r'CONDITIONAL PASS', r'VALIDATION PASSES', r'VALIDATION FAILS'],
    'V55': [r'V5\.5', r'TEMPORAL OOS', r'OOS PASSES', r'OOS CONDITIONAL PASS', r'OOS FAILS', r'PART18 \+ PEAK50'],
}

RUNTIME_MARKERS = re.compile(r'(DECISION:|NEXT:|TOTAL_|NET\b|GROSS\b|WIN.?RATE|MDD|DRAWDOWN|PROFIT FACTOR|TRADES?\b|COST|OOS|PASS|FAIL|\+\d+\.\d+%|-\d+\.\d+%)', re.I)
EXCLUDE_PARTS = ('engine_recovery_', 'engine_runtime_recovery_', 'ENGINE_RECOVERY_SUMMARY', 'metric_hits.txt', 'version_hits.txt')
EXCLUDE_EXT = {'.py', '.pyc', '.sh'}
TEXT_EXT = {'.txt','.log','.md','.csv','.json','.out'}

files=[]
seen=set()
for root in ROOTS:
    if not root.exists():
        continue
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        s=str(p)
        if any(x in s for x in EXCLUDE_PARTS):
            continue
        if p.suffix.lower() in EXCLUDE_EXT:
            continue
        if p.suffix.lower() not in TEXT_EXT and not p.name.endswith(('.stdout','.stderr')):
            continue
        try:
            rp=str(p.resolve())
        except Exception:
            rp=s
        if rp in seen:
            continue
        seen.add(rp); files.append(p)

results={k:[] for k in TARGETS}
for p in files:
    try:
        if p.stat().st_size > 8_000_000:
            continue
        text=p.read_text('utf-8', errors='ignore')
    except Exception:
        continue
    lines=text.splitlines()
    for i,line in enumerate(lines,1):
        if not RUNTIME_MARKERS.search(line):
            continue
        for k,pats in TARGETS.items():
            if any(re.search(pt,line,re.I) for pt in pats):
                lo=max(0,i-4); hi=min(len(lines),i+3)
                ctx=' | '.join(x.strip() for x in lines[lo:hi] if x.strip())
                score=0
                if '/tmp/' in str(p): score+=4
                if re.search(r'DECISION:|NEXT:',ctx,re.I): score+=4
                if re.search(r'NET|WIN.?RATE|MDD|PROFIT FACTOR|TRADES?',ctx,re.I): score+=3
                if re.search(r'\+\d+\.\d+%|-\d+\.\d+%',ctx): score+=2
                if 'source' in p.name.lower() or 'summary' in p.name.lower(): score-=3
                results[k].append((score,str(p),i,ctx[:1800]))

with OUT.open('w',encoding='utf-8') as f:
    f.write('# TREND RUNTIME CONFIRMATION\n\n')
    f.write('Source-only code branches are excluded where possible. Prefer /tmp and concrete log/report outputs.\n\n')
    for k,items in results.items():
        items=sorted(items,key=lambda x:(-x[0],x[1],x[2]))
        f.write(f'## {k} — {len(items)} candidate runtime lines\n\n')
        for score,path,ln,ctx in items[:80]:
            f.write(f'- SCORE={score} `{path}:{ln}` — {ctx}\n')
        f.write('\n')

print(f'RUNTIME_CONFIRM_OK={OUT}')
for k,items in results.items():
    strong=sum(1 for x in items if x[0]>=8)
    print(f'{k}_CANDIDATES={len(items)} STRONG={strong}')
    for score,path,ln,ctx in sorted(items,key=lambda x:(-x[0],x[1],x[2]))[:6]:
        print(f'  SCORE={score} {path}:{ln} :: {ctx[:700]}')
print(f'REPORT_DIR={OUTDIR}')
