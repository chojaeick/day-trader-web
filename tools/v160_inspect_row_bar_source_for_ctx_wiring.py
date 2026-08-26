#!/usr/bin/env python3
"""V160 inspect actual live 1m-bar source available to refresh_usa_tracker row assembly.
READ ONLY. No service/order/strategy mutation.
"""
from pathlib import Path
import re

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
S=P.read_text(errors='ignore')
L=S.splitlines()
print('=== V160 INSPECT ROW BAR SOURCE FOR CTX WIRING ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

# refresh_usa_tracker block boundaries
start=next((i for i,x in enumerate(L) if re.match(r'\s*def refresh_usa_tracker\s*\(',x)),None)
if start is None:
    print('REFRESH_USA_TRACKER_NOT_FOUND'); raise SystemExit(2)
indent=len(L[start])-len(L[start].lstrip())
end=len(L)
for i in range(start+1,len(L)):
    x=L[i]
    if x.strip() and (len(x)-len(x.lstrip())==indent) and re.match(r'\s*def\s+',x):
        end=i; break
print('REFRESH_BLOCK=',start+1,'..',end)

# print only useful lines plus short context around hits
patterns=[
    r'ticks_to_bars',r'\bb1\b',r'bar',r'minute',r'df',r"\['time'\]",r'rows\.append',
    r"'market'\s*:\s*'USA'",r'row\s*=\s*\{',r'r\s*=\s*\{',r'day_open',r'prev_day_high',r'prev_day_low',
    r'_paper_williams_step',r'_finalize\(',r'williams_frozen_ctx'
]
hits=[]
for i in range(start,end):
    if any(re.search(p,L[i]) for p in patterns): hits.append(i)
print('HIT_LINES=',[i+1 for i in hits])
shown=set()
for h in hits:
    a=max(start,h-3); b=min(end,h+4)
    key=(a,b)
    if key in shown: continue
    shown.add(key)
    print(f'--- CONTEXT {a+1}:{b} ---')
    for j in range(a,b): print(f'{j+1}: {L[j]}')

# assignment summary: likely dataframe vars and builder call candidates
print('=== ASSIGNMENT SUMMARY ===')
for i in range(start,end):
    x=L[i]
    if re.search(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*=.*ticks_to_bars',x) or re.search(r'\b(b1|bars1|one|one_min|bars_1m)\b\s*=',x):
        print(i+1,x.strip())
print('BUILDER_CALL_COUNT=',sum('_v142_build_usa_frozen_ctx(' in x for x in L[start:end]))
print('PAPER_STEP_CALL_COUNT=',sum('_paper_williams_step(' in x for x in L[start:end]))
print('NEXT=PATCH_ONLY_AFTER_IDENTIFYING_EXACT_LIVE_B1_VARIABLE_AND_ROW_ASSEMBLY_POINT')
