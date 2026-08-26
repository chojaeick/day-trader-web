#!/usr/bin/env python3
import json, urllib.request, urllib.parse

BASE='http://127.0.0.1:8000'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM','PLTR']
print('=== V188B CALL RUNNING-API RANK SYMBOL PROBE ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

for sym in SYMS:
    url=BASE + '/api/debug/usa-rank-symbol-probe?' + urllib.parse.urlencode({'symbol':sym})
    try:
        with urllib.request.urlopen(url, timeout=45) as r:
            body=r.read().decode('utf-8','replace')
            print('SYMBOL',sym,'HTTP',r.status)
            try:
                d=json.loads(body)
                print(json.dumps(d, ensure_ascii=False, sort_keys=True))
            except Exception:
                print(body[:5000])
    except Exception as e:
        print('SYMBOL',sym,'ERROR',repr(e))

print('NEXT=COMPARE_STK_CD_STEX_TP_AND_QUOTE_MATRIX__PATCH_CODE_MAP_IF_MISMATCH__ELSE_CONFIRM_F5_ELIGIBILITY_ISSUE')
