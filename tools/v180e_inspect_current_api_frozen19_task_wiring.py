#!/usr/bin/env python3
from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V180E INSPECT CURRENT API FROZEN19 TASK WIRING ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('FILE=',p,'EXISTS=',p.exists())
if not p.exists(): raise SystemExit(2)
lines=p.read_text(encoding='utf-8').splitlines()
patterns=('frozen19','Frozen19','frozen_19','frozen_paper','websocket_forever','create_task','startup','on_event','lifespan')
hits=[]
for i,line in enumerate(lines,1):
    if any(x.lower() in line.lower() for x in patterns): hits.append(i)
print('HITS=',hits)
shown=set()
for i in hits:
    a=max(1,i-8); b=min(len(lines),i+12)
    key=(a,b)
    if key in shown: continue
    shown.add(key)
    print(f'--- CONTEXT {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {lines[n-1]}')
print('NEXT=BUILD_EXACT_V180F_DISABLE_PATCH_FROM_VISIBLE_TASK_WIRING; KEEP_V180C_SINGLE_WS_FROZEN_PRIORITY')
