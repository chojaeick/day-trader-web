#!/usr/bin/env python3
"""V160B inspect actual _usa_row/_finalize bar source for frozen context wiring.
READ ONLY. No strategy/order/service mutation.
"""
from pathlib import Path
import re
P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
S=P.read_text(errors='ignore').splitlines()
print('=== V160B INSPECT _usa_row + _finalize BAR SOURCE ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

def find_defs(name):
    out=[]
    pat=re.compile(r'^\s*def\s+'+re.escape(name)+r'\s*\(')
    for i,l in enumerate(S,1):
        if pat.search(l): out.append(i)
    return out

def dump(a,b):
    a=max(1,a); b=min(len(S),b)
    for i in range(a,b+1): print(f'{i}: {S[i-1]}')

for name in ['_usa_row','_finalize']:
    hits=find_defs(name)
    print(name,'DEFS=',hits)
    for h in hits:
        # dump a broad contiguous function slice until next def at same indent or max 260 lines
        indent=len(S[h-1])-len(S[h-1].lstrip())
        end=min(len(S),h+260)
        for j in range(h+1,min(len(S),h+260)+1):
            l=S[j-1]
            if l.strip().startswith('def ') and (len(l)-len(l.lstrip()))==indent:
                end=j-1; break
        print(f'--- {name} CONTEXT {h}:{end} ---')
        dump(h,end)

# Global likely bar-source hits near these methods and tracker flow.
terms=['ticks_to_bars','b1','bars1','bars_1','minute_bars','historical_minute_bars','db.ticks','ticks(','_bars','prev_day_high','prev_day_low','day_open','session_open']
print('=== GLOBAL BAR SOURCE HITS ===')
for i,l in enumerate(S,1):
    if any(t in l for t in terms):
        if 900 <= i <= 4050:
            print(f'{i}: {l}')
print('NEXT=IDENTIFY_EXACT_B1_SOURCE_THEN_PATCH_SINGLE_CTX_ASSIGNMENT')
