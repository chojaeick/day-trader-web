#!/usr/bin/env python3
import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V191 FROZEN19 CURRENT FRESHNESS REBASELINE ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('DB=',DB,'EXISTS=',DB.exists())
con=sqlite3.connect(DB)
cur=con.cursor()
now=datetime.now(timezone.utc)
rows=[]
for s in SYMS:
    r=cur.execute('select id, ts, price, qty from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    if not r:
        rows.append((s,None,None,None,None))
        continue
    try:
        dt=datetime.fromisoformat(str(r[1]).replace('Z','+00:00'))
        age=(now-dt).total_seconds()
    except Exception:
        age=None
    rows.append((s,r[0],r[1],r[2],age))
for s,i,ts,p,age in rows:
    print('SYMBOL',s,'ID',i,'TS',ts,'PRICE',p,'AGE_SEC',None if age is None else round(age,1))
fresh=[s for s,_,_,_,a in rows if a is not None and a<=180]
lag=[s for s,_,_,_,a in rows if a is not None and 180<a<=900]
stale=[s for s,_,_,_,a in rows if a is None or a>900]
print('FRESH_LE_180S',len(fresh),fresh)
print('LAG_180_900S',len(lag),lag)
print('STALE_GT_900S',len(stale),stale)
print('NEXT=TARGET_ONLY_CURRENT_STALE_SYMBOLS_FOR_F5_DELIVERY_AUDIT')
