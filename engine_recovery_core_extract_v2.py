from __future__ import annotations
import re, sys
from pathlib import Path

root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else None
if not root or not root.exists():
    print('USAGE: python engine_recovery_core_extract_v2.py /path/to/engine_recovery_YYYYMMDD_HHMMSS')
    raise SystemExit(2)

src = root / 'ENGINE_RECOVERY_SUMMARY.md'
if not src.exists():
    print(f'MISSING={src}')
    raise SystemExit(3)

text = src.read_text(encoding='utf-8', errors='ignore').splitlines()

# Prefer evidence with explicit outcomes/metrics and version identifiers.
strong = re.compile(r'\b(PASS|FAIL|PROMOTE|REJECT|RETEST|KEEP_COMPONENT|MDD|WIN.?RATE|RETURN|PNL|PROFIT|SHARPE|DRAWDOWN|TRADES?|CAUSAL|OOS|COST|STRESS)\b', re.I)
metric = re.compile(r'[-+]?\d+(?:\.\d+)?\s*%|[-+]?\d+(?:\.\d+)?R\b|\b\d+\s*(?:trades?|cases?|days?)\b', re.I)
version = re.compile(r'\b(?:V[1-9](?:\.\d+)*|PART\s*18|TREND|SCALP|REBOUND|MACD|RSI)\b', re.I)

sections = {}
current = 'OTHER'
for line in text:
    m = re.match(r'^###\s+([A-Z_]+)', line)
    if m:
        current = m.group(1)
        sections.setdefault(current, [])
        continue
    if line.startswith('- `') and ' — ' in line:
        score = 0
        if strong.search(line): score += 3
        if metric.search(line): score += 3
        if version.search(line): score += 2
        if re.search(r'PASS|FAIL|PROMOTE|REJECT', line, re.I): score += 3
        if re.search(r'causal|oos|cost|stress', line, re.I): score += 2
        if score >= 5:
            sections.setdefault(current, []).append((score, line))

out = root / 'ENGINE_RECOVERY_CORE_EVIDENCE.md'
with out.open('w', encoding='utf-8') as f:
    f.write('# ENGINE RECOVERY CORE EVIDENCE\n\n')
    f.write('Evidence-only shortlist. No final verdict is inferred automatically.\n\n')
    total = 0
    for name in ['PASS_FAIL','OOS_CAUSAL','COST_STRESS','TREND','SCALP','REBOUND','RSI_MACD','VERSIONS']:
        vals = sections.get(name, [])
        # Stable de-dup by exact line, highest score first.
        seen=set(); ranked=[]
        for score,line in sorted(vals, key=lambda x:(-x[0], x[1])):
            if line in seen: continue
            seen.add(line); ranked.append((score,line))
        ranked = ranked[:80]
        total += len(ranked)
        f.write(f'## {name} ({len(ranked)})\n\n')
        for score,line in ranked:
            f.write(line+'\n')
        f.write('\n')
    f.write('## Classification rule\n\n')
    f.write('Every recovered experiment must later be assigned exactly one: PROMOTE / KEEP_COMPONENT / RETEST / REJECT / INVALID_TEST / UNKNOWN_RECOVERY.\n')

print(f'CORE_OK={out}')
print(f'CORE_LINES={sum(1 for x in out.read_text(encoding="utf-8").splitlines() if x.startswith("- `"))}')
print('--- TOP EVIDENCE PREVIEW ---')
preview=[x for x in out.read_text(encoding='utf-8').splitlines() if x.startswith('- `')][:80]
for x in preview:
    print(x)
