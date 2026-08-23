from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else None
if not ROOT or not ROOT.exists():
    print('USAGE: python engine_recovery_summarize_v1.py /path/to/engine_recovery_YYYYMMDD_HHMMSS')
    raise SystemExit(2)

OUT = ROOT / 'ENGINE_RECOVERY_SUMMARY.md'

TEXT_EXTS = {'.txt','.md','.log','.json','.csv','.py','.sh','.ini','.yaml','.yml'}
MAX_PER_FILE = 2_000_000
PATTERNS = [
    re.compile(r'\b(PASS|FAIL|PROMOTE|REJECT|RETEST|OOS|MDD|WIN.?RATE|RETURN|PNL|PROFIT|SHARPE|DRAWDOWN|COST|STRESS|CAUSAL|TREND|SCALP|REBOUND|MACD|RSI|PART\s*18|V[1-9](?:\.\d+)*)\b', re.I),
]

files = sorted([p for p in ROOT.rglob('*') if p.is_file()])
lines = []
for p in files:
    rel = p.relative_to(ROOT)
    try:
        size = p.stat().st_size
    except Exception:
        size = -1
    lines.append((str(rel), size))

hits = []
for p in files:
    if p.suffix.lower() not in TEXT_EXTS:
        continue
    try:
        raw = p.read_bytes()[:MAX_PER_FILE]
        text = raw.decode('utf-8', errors='ignore')
    except Exception:
        continue
    for idx, line in enumerate(text.splitlines(), 1):
        if any(rx.search(line) for rx in PATTERNS):
            s = line.strip()
            if s:
                hits.append((str(p.relative_to(ROOT)), idx, s[:500]))

# targeted buckets
buckets = {
    'TREND': [], 'SCALP': [], 'REBOUND': [], 'RSI_MACD': [],
    'OOS_CAUSAL': [], 'COST_STRESS': [], 'PASS_FAIL': [], 'VERSIONS': []
}
for rel, ln, text in hits:
    t = text.upper()
    item = (rel, ln, text)
    if 'TREND' in t: buckets['TREND'].append(item)
    if 'SCALP' in t: buckets['SCALP'].append(item)
    if 'REBOUND' in t: buckets['REBOUND'].append(item)
    if 'RSI' in t or 'MACD' in t: buckets['RSI_MACD'].append(item)
    if 'OOS' in t or 'CAUSAL' in t: buckets['OOS_CAUSAL'].append(item)
    if 'COST' in t or 'STRESS' in t: buckets['COST_STRESS'].append(item)
    if 'PASS' in t or 'FAIL' in t or 'PROMOTE' in t or 'REJECT' in t: buckets['PASS_FAIL'].append(item)
    if re.search(r'\bV[1-9](?:\.\d+)*\b', t): buckets['VERSIONS'].append(item)

now = datetime.now(timezone.utc).isoformat()
with OUT.open('w', encoding='utf-8') as f:
    f.write('# ENGINE RECOVERY SUMMARY\n\n')
    f.write(f'- Generated: {now}\n')
    f.write(f'- Source: `{ROOT}`\n')
    f.write(f'- Files scanned: {len(files)}\n')
    f.write(f'- Metric/version evidence lines: {len(hits)}\n\n')

    f.write('## 1. Recovery File Inventory\n\n')
    for rel, size in lines[:500]:
        f.write(f'- `{rel}` ({size} bytes)\n')
    if len(lines) > 500:
        f.write(f'- ... {len(lines)-500} more files omitted from this index\n')

    f.write('\n## 2. Evidence Buckets\n')
    for name, items in buckets.items():
        f.write(f'\n### {name} ({len(items)})\n')
        for rel, ln, text in items[:250]:
            safe = text.replace('`','\\`')
            f.write(f'- `{rel}:{ln}` — {safe}\n')
        if len(items) > 250:
            f.write(f'- ... {len(items)-250} more lines omitted\n')

    f.write('\n## 3. Decision Discipline\n\n')
    f.write('This file is evidence extraction only. Do not infer a final engine verdict from filenames alone.\n')
    f.write('Each recovered experiment must be classified later as one of: PROMOTE / KEEP_COMPONENT / RETEST / REJECT / INVALID_TEST / UNKNOWN_RECOVERY.\n')
    f.write('Where exact metrics are unavailable, mark UNKNOWN_RECOVERY rather than reconstructing from memory.\n')

print(f'SUMMARY_OK={OUT}')
print(f'FILES={len(files)}')
print(f'HITS={len(hits)}')
for k,v in buckets.items():
    print(f'{k}={len(v)}')
