#!/usr/bin/env python3
"""V166 inspect actual api.py DB object creation/injection for USA tracker.
READ ONLY. No strategy/order/service mutation.
"""
from pathlib import Path
import re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
print('=== V166 INSPECT API DB INJECTION FOR USA TRACKER ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
for p in (API,ENG): print('FILE',p,'EXISTS=',p.exists())
if not API.exists(): raise SystemExit(2)
S=API.read_text(errors='ignore').splitlines()
terms=['refresh_usa_tracker','db =','db=','DB(','Database(','SQLite','daytrader.db','CleanEngine(','v4 =','v4=']
hits=[]
for i,l in enumerate(S,1):
    if any(t in l for t in terms): hits.append(i)
print('HITS=',hits)
shown=[]
for h in hits:
    if any(abs(h-x)<18 for x in shown): continue
    shown.append(h)
    a=max(1,h-18); b=min(len(S),h+28)
    print(f'--- API CONTEXT {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {S[n-1]}')

# Also identify candidate classes/functions in runtime files that expose ticks().
for p in [API,ENG,Path('/home/ubuntu/day-trader-api/live_server/db.py')]:
    if not p.exists(): continue
    L=p.read_text(errors='ignore').splitlines()
    print('=== TICKS PROVIDER HITS',p,'===')
    for i,l in enumerate(L,1):
        if re.search(r'^\s*def\s+ticks\s*\(',l) or 'def ticks(' in l or 'class ' in l and ('DB' in l or 'Database' in l):
            print(f'{i}: {l}')
print('NEXT=BUILD_V167_USING_EXACT_API_DB_OBJECT; DO_NOT_GUESS_DB_OWNERSHIP')
