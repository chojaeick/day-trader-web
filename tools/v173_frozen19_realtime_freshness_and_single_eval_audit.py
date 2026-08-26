#!/usr/bin/env python3
import os,sys,time,json,sqlite3,urllib.request
from datetime import datetime,timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
API='http://127.0.0.1:8000'
CORE=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V173 FROZEN19 REALTIME FRESHNESS + SINGLE EVAL AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
now=datetime.now(timezone.utc)
print('NOW_UTC=',now.isoformat())
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
fresh=[]; stale=[]; missing=[]
for s in CORE:
    r=con.execute('select ts,price from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    if not r:
        missing.append(s); print('SYMBOL',s,'LAST=None AGE_SEC=None CLASS=MISSING'); continue
    ts=str(r['ts'])
    try:
        dt=datetime.fromisoformat(ts.replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        age=(now-dt.astimezone(timezone.utc)).total_seconds()
    except Exception:
        age=None
    cls='FRESH' if age is not None and age<=180 else 'STALE'
    (fresh if cls=='FRESH' else stale).append(s)
    print('SYMBOL',s,'LAST=',ts,'AGE_SEC=',None if age is None else round(age,1),'CLASS=',cls)
print('FRESH_COUNT=',len(fresh),fresh)
print('STALE_COUNT=',len(stale),stale)
print('MISSING_COUNT=',len(missing),missing)

# API status markers / paper account
for path in ['/api/v4/USA/status','/api/v4/USA/tracker']:
    try:
        with urllib.request.urlopen(API+path,timeout=10) as x:
            d=json.loads(x.read().decode())
        print('HTTP',path,'=200')
        if path.endswith('status'):
            print('SESSION=',d.get('session'))
            pa=d.get('paper_account') or {}
            print('PAPER_POSITIONS=',[(p.get('symbol'),p.get('qty'),p.get('avg_entry')) for p in (pa.get('positions') or [])])
    except Exception as e:
        print('HTTP',path,'ERR=',repr(e))

# Static runtime markers for V171 single-eval feed
p='/home/ubuntu/day-trader-api/live_server/v4_engine.py'
try:
    txt=open(p,encoding='utf-8').read()
    markers=['FROZEN_CORE_19','WUF_FEED_LAST_BAR','completed','_paper_williams_step']
    for m in markers:
        print('MARKER',m,'COUNT=',txt.count(m))
except Exception as e:
    print('ENGINE_READ_ERR=',repr(e))

# Recent logs relevant to V171 feed / websocket
os.system("journalctl -u day-trader-api --since '15 minutes ago' --no-pager 2>/dev/null | grep -E 'WebSocket universe refreshed|V171|frozen|WUF' | tail -80")
print('REALTIME_FRESHNESS_PASS=', len(fresh)>=15)
print('NEXT=IF_FRESH_LOW_DIAGNOSE_WS_F5_DELIVERY_OR_EXCHANGE_MAPPING; IF_FRESH_HIGH_VERIFY_CTX_EVAL_ON_NEW_COMPLETED_BAR')
