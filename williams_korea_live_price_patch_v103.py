#!/usr/bin/env python3
from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()
orig=s

# 1) expose latest causal 1m close from the already-fetched KR minute chart gate
old="                'latest_1m':str(a.get('time')),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),"
new="                'latest_1m':str(a.get('time')),\n\n                'latest_price':_f(a.get('close')),\n\n                'latest_5m':five.iloc[-1]['dt'].isoformat(),"
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: latest_1m gate block')
s=s.replace(old,new,1)

# 2) KR pulse/finder can be empty while minute-chart gate is healthy; use gate latest close as live fallback
old2="                'price':_f(p.get('price',f.get('price'))),"
new2="                'price':(_f(p.get('price')) or _f(f.get('price')) or _f(gate.get('latest_price'))),"
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: KOREA tracker price assignment')
s=s.replace(old2,new2,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
p.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('KOREA_PRICE_SOURCE=pulse -> finder -> latest causal 1m close')
print('ORDER_BEHAVIOR_CHANGED=NO')
