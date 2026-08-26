#!/usr/bin/env python3
import json, urllib.request, urllib.error

BASE='http://127.0.0.1:8000'
print('=== V187C PROBE SYMBOL CODES VIA RUNNING API ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

paths=[
    '/api/v4/USA/finder',
    '/api/v4/USA/tracker',
    '/api/v4/USA/status',
    '/api/v4/USA/frozen-paper',
]
for p in paths:
    try:
        with urllib.request.urlopen(BASE+p, timeout=20) as r:
            body=r.read().decode('utf-8','replace')
            print('HTTP',p,r.status,'LEN',len(body))
            try:
                d=json.loads(body)
            except Exception:
                print('BODY_HEAD',body[:500]); continue
            if isinstance(d,dict):
                for k in ('rows','light_rows','candidates','discovery','tracker','finder'):
                    v=d.get(k)
                    if isinstance(v,list):
                        print('KEY',k,'COUNT',len(v))
                        for row in v[:100]:
                            if isinstance(row,dict):
                                sym=str(row.get('symbol') or row.get('stk_cd') or '').upper()
                                if sym in {'AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM'}:
                                    print('ROW',sym,json.dumps(row,ensure_ascii=False,default=str)[:1200])
                print('TOP_KEYS',sorted(d.keys())[:80])
    except Exception as e:
        print('ERR',p,repr(e))

# Inspect source endpoints exposed by OpenAPI, looking for ranking/quote/discovery diagnostics.
try:
    with urllib.request.urlopen(BASE+'/openapi.json', timeout=20) as r:
        spec=json.load(r)
    candidates=[]
    for p in (spec.get('paths') or {}):
        low=p.lower()
        if any(x in low for x in ('rank','quote','discover','usa')):
            candidates.append(p)
    print('OPENAPI_CANDIDATES',candidates[:200])
except Exception as e:
    print('OPENAPI_ERR',repr(e))

print('NEXT=USE_VISIBLE_RUNNING_API_ENDPOINTS_OR_ADD_READ_ONLY_RUNTIME_DIAGNOSTIC_ENDPOINT_FOR_RANKING_SYMBOL_CODE')
