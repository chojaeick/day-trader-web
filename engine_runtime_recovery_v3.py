from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime, timezone

ROOTS = [
    Path('/home/ubuntu/day-trader-api'),
    Path('/home/ubuntu/day-trader-api-repo'),
    Path('/tmp'),
]
OUTDIR = Path('/home/ubuntu/day-trader-api/engine_runtime_recovery_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
OUTDIR.mkdir(parents=True, exist_ok=True)

TARGETS = {
    'V51': [r'trend_v51', r'V5\.1', r'FULL_CONFIRMATION'],
    'V52': [r'trend_v52', r'V5\.2', r'EXIT_CAPTURE', r'MINIMAL_PART'],
    'V53': [r'trend_v53', r'V5\.3', r'PART18', r'EXIT_CAPTURE'],
    'V54': [r'trend_v54', r'V5\.4', r'COST/STRESS', r'COST_STRESS'],
    'V55': [r'trend_v55', r'V5\.5', r'TEMPORAL OOS', r'PEAK50'],
}
METRIC_RX = re.compile(r'(DECISION|PASS|FAIL|CONDITIONAL|NET|RETURN|PNL|P&L|WIN.?RATE|TRADES?|MDD|DRAWDOWN|COST|SLIPPAGE|STRESS|OOS|SHARPE|PROFIT.?FACTOR|MFE|MAE|PART18|PEAK50)', re.I)
SOURCE_EXT = {'.py', '.sh', '.pyc'}
TEXT_EXT = {'.txt','.log','.md','.csv','.json','.out','.err','.yaml','.yml','.ini',''}
SKIP_PARTS = {'venv','venv-ui','.git','__pycache__','site-packages','node_modules'}
MAX_FILE = 8_000_000

# collect candidate text artifacts, newest first
files=[]
seen=set()
for root in ROOTS:
    if not root.exists():
        continue
    for p in root.rglob('*'):
        try:
            if not p.is_file(): continue
            if any(x in p.parts for x in SKIP_PARTS): continue
            rp=str(p.resolve())
            if rp in seen: continue
            seen.add(rp)
            if p.suffix.lower() in SOURCE_EXT: continue
            if p.suffix.lower() not in TEXT_EXT: continue
            if p.stat().st_size > MAX_FILE: continue
            files.append(p)
        except Exception:
            pass
files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

# explicitly include shell history and recovery evidence artifacts
extra=[Path('/home/ubuntu/.bash_history')]
for e in extra:
    if e.exists() and str(e.resolve()) not in seen:
        files.append(e)

hits={k:[] for k in TARGETS}
all_metric=[]
for p in files:
    try:
        text=p.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        continue
    lines=text.splitlines()
    upper=text.upper()
    for key,pats in TARGETS.items():
        if not any(re.search(pt, text, re.I) for pt in pats):
            continue
        for i,line in enumerate(lines):
            if METRIC_RX.search(line) and any(re.search(pt, '\n'.join(lines[max(0,i-4):min(len(lines),i+5)]), re.I) for pt in pats):
                ctx='\n'.join(lines[max(0,i-3):min(len(lines),i+4)])
                hits[key].append((p, i+1, ctx))
                if len(hits[key]) >= 400: break
    for i,line in enumerate(lines):
        if METRIC_RX.search(line) and ('trend_v5' in line.lower() or 'part18' in line.lower() or 'peak50' in line.lower()):
            all_metric.append((p,i+1,line.strip()))

# rank likely runtime output over source echoes
runtime_name_rx=re.compile(r'(result|report|summary|output|stdout|run|backtest|validation|oos|stress|audit|log)', re.I)
def score(item):
    p,ln,ctx=item
    s=0
    name=p.name.lower()
    if runtime_name_rx.search(name): s+=5
    if str(p).startswith('/tmp/'): s+=3
    if '/engine_recovery_' in str(p): s+=2
    if re.search(r'\bDECISION\b',ctx,re.I): s+=4
    if re.search(r'\b(?:NET|RETURN|PNL|WIN.?RATE|MDD|TRADES?)\b',ctx,re.I): s+=3
    if 'print(' in ctx or 'f"' in ctx or "f'" in ctx: s-=5
    if p.name in {'metric_hits.txt','version_hits.txt'}: s-=2
    return s

report=[]
report.append('# TARGETED ENGINE RUNTIME RECOVERY V3')
report.append('')
report.append(f'Generated: {datetime.now(timezone.utc).isoformat()}')
report.append('Rule: source code branches are not treated as executed results. Prioritize concrete runtime/output artifacts.')
report.append('')
for key in TARGETS:
    uniq=[]; keys=set()
    for item in sorted(hits[key], key=score, reverse=True):
        sig=(str(item[0]),item[1],item[2])
        if sig in keys: continue
        keys.add(sig); uniq.append(item)
    report.append(f'## {key} — candidates={len(uniq)}')
    for p,ln,ctx in uniq[:80]:
        report.append(f'### SCORE={score((p,ln,ctx))} `{p}:{ln}`')
        report.append('```')
        report.append(ctx[:1800])
        report.append('```')
    report.append('')

out=OUTDIR/'TARGETED_RUNTIME_EVIDENCE.md'
out.write_text('\n'.join(report), encoding='utf-8')

# compact terminal preview: only strong candidates
print(f'RUNTIME_RECOVERY_OK={out}')
for key in TARGETS:
    ranked=sorted(hits[key], key=score, reverse=True)
    strong=[x for x in ranked if score(x)>=6]
    print(f'{key}_HITS={len(hits[key])} STRONG={len(strong)}')
    for p,ln,ctx in strong[:5]:
        one=' | '.join(x.strip() for x in ctx.splitlines() if x.strip())
        print(f'  SCORE={score((p,ln,ctx))} {p}:{ln} :: {one[:700]}')
print(f'REPORT_DIR={OUTDIR}')
