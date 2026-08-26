#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, sys

P=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
print('=== V186B RETARGET EXCHANGE-CODE PROBE FROM RUNTIME ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('FILE=',P,'EXISTS=',P.exists())
if not P.exists():
    sys.exit(2)
s=P.read_text()
patterns=[r'def\s+_ws_items\s*\(',r'jmcode',r'stex_tp',r"'item'",r'\"item\"',r'websocket_forever']
hits=[]
lines=s.splitlines()
for i,line in enumerate(lines,1):
    if any(re.search(p,line) for p in patterns):
        hits.append(i)
print('HITS=',hits)
for i in hits:
    a=max(1,i-8); b=min(len(lines),i+12)
    print(f'--- CONTEXT {a}:{b} ---')
    for n in range(a,b+1):
        print(f'{n}: {lines[n-1]}')
print('NEXT=BUILD_V186C_EXACT_AMD_ND_NY_NA_PROBE_FROM_VISIBLE_ITEM_CONSTRUCTION')
