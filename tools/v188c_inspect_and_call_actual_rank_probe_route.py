#!/usr/bin/env python3
from pathlib import Path
import re, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V188C INSPECT + CALL ACTUAL RANK PROBE ROUTE ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
text=API.read_text()
lines=text.splitlines()
hits=[]
for i,l in enumerate(lines,1):
    if 'V188' in l or 'rank-symbol' in l.lower() or 'symbol-probe' in l.lower() or 'debug' in l.lower() and 'rank' in l.lower():
        hits.append(i)
print('HITS=',hits)
for i in hits:
    a=max(1,i-4); b=min(len(lines),i+8)
    print(f'--- CONTEXT {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {lines[n-1]}')

routes=[]
for i,l in enumerate(lines):
    if '@app.get(' in l and i+1 < len(lines):
        block='\n'.join(lines[max(0,i-2):min(len(lines),i+6)])
        if 'V188' in block or ('rank' in block.lower() and ('probe' in block.lower() or 'debug' in block.lower())):
            m=re.search(r"@app\.get\((['\"])(.+?)\1\)",l)
            if m: routes.append(m.group(2))
print('ROUTES=',routes)

symbols=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM','PLTR']
for route in routes:
    print('TRY_ROUTE',route)
    for sym in symbols:
        path=route.replace('{symbol}',sym).replace('{sym}',sym)
        url='http://127.0.0.1:8000'+path
        try:
            with urllib.request.urlopen(url,timeout=30) as r:
                body=r.read().decode('utf-8','replace')
                print('SYMBOL',sym,'HTTP',r.status,'BODY',body[:2500])
        except Exception as e:
            print('SYMBOL',sym,'ERROR',repr(e))
print('NEXT=IF_ROUTE_FOUND_USE_RESPONSE; IF_NO_ROUTE_FIX_V188_ENDPOINT_EXACTLY')
