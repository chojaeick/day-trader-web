#!/usr/bin/env python3
from pathlib import Path
P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
S=P.read_text(errors='ignore').splitlines()
print('=== V159 FROZEN CONTEXT WIRING INSPECT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
keys=['def _v142','williams_frozen_ctx','_v140_usa_frozen_williams_eval','_paper_williams_step','refresh_usa_tracker','paper_result=self._paper_williams_step']
hits=[]
for i,l in enumerate(S,1):
    if any(k in l for k in keys): hits.append(i)
print('HITS=',hits)
shown=set()
for i in hits:
    a=max(1,i-28); b=min(len(S),i+45)
    if any(abs(i-x)<20 for x in shown): continue
    shown.add(i)
    print(f'--- CONTEXT {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {S[n-1]}')
print('=== SIMPLE WIRING FLAGS ===')
text='\n'.join(S)
print('BUILDER_DEFINED=', 'def _v142' in text)
print('CTX_ASSIGN_COUNT=', text.count("['williams_frozen_ctx']")+text.count('"williams_frozen_ctx"'))
print('EVAL_READS_CTX=', "get('williams_frozen_ctx')" in text or 'get("williams_frozen_ctx")' in text)
print('PAPER_CALL_PRESENT=', 'paper_result=self._paper_williams_step' in text)
print('NEXT=PATCH_EXACT_CONTEXT_WIRING_ONLY')
