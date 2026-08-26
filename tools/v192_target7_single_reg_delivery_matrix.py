#!/usr/bin/env python3
import sqlite3, time, urllib.request, json
from datetime import datetime, timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
TARGETS=['AMD','AMZN','ARM','AVGO','GOOGL','ORCL','TSM']
CONTROLS=['NVDA','QQQ']
ALL=TARGETS+CONTROLS
print('=== V192 TARGET7 SINGLE-REG DELIVERY MATRIX ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

def latest_tick(sym):
    con=sqlite3.connect(DB)
    try:
        r=con.execute("select id,ts,price,qty from ticks where symbol=? order by id desc limit 1",(sym,)).fetchone()
        return r
    finally:
        con.close()

def quote(sym):
    url=f'http://127.0.0.1:8000/api/v4/USA/debug/rank-symbol/{sym}'
    with urllib.request.urlopen(url,timeout=30) as r:
        return json.loads(r.read().decode())

for sym in ALL:
    base=latest_tick(sym)
    print('SYMBOL',sym,'BASE_TICK',base)
    try:
        q=quote(sym)
        qm=q.get('quote_matrix') or {}
        ok=[]
        for ex,v in qm.items():
            if isinstance(v,dict) and 'price' in v:
                ok.append((ex,v.get('price'),v.get('updated_at')))
        print('QUOTE_OK',sym,ok)
        print('RANK_HITS',sym,q.get('hits'))
    except Exception as e:
        print('QUOTE_ERROR',sym,repr(e))

print('NOTE=This matrix establishes current REST liveness + existing tick freshness only.')
print('NEXT=PATCH TEMP SINGLE-SYMBOL REG HARNESS FOR ONLY THE 7 CURRENT STALE SYMBOLS IF REST IS LIVE')
