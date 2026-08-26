#!/usr/bin/env python3
from pathlib import Path
import re
P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
S=P.read_text(errors='ignore').splitlines()
print('=== V168 INSPECT V161 CTX WIRING LOCATION ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
keys=['V161','williams_frozen_ctx','_v142_build_usa_frozen_ctx','def _usa_row','return row','return r','_paper_williams_step']
hits=[]
for i,l in enumerate(S,1):
    if any(k in l for k in keys): hits.append(i)
print('HITS=',hits)
shown=[]
for i in hits:
    if any(abs(i-j)<18 for j in shown): continue
    shown.append(i)
    a=max(1,i-22); b=min(len(S),i+35)
    print(f'--- CONTEXT {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {S[n-1]}')
text='\n'.join(S)
print('CTX_ASSIGNMENTS=',text.count("row['williams_frozen_ctx']")+text.count("r['williams_frozen_ctx']"))
print('BUILDER_CALLS=',text.count('_v142_build_usa_frozen_ctx(')-1)
print('NEXT=PATCH_EXACT_USA_ROW_RETURN_PATH_ONLY_IF_V161_WIRING_IS_BEFORE_RETURN_AND_HAS_B1')
