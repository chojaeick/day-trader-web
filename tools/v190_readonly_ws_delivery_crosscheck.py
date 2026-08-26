#!/usr/bin/env python3
import sqlite3, json, subprocess
from pathlib import Path
from collections import Counter

DB='/home/ubuntu/day-trader-api/daytrader.db'
K=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
print('=== V190 READONLY WS DELIVERY CROSSCHECK ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('KIWOOM_EXISTS=',K.exists())

src=K.read_text() if K.exists() else ''
for needle in ['self.db.add_raw(raw, now)','for symbol,price,qty,cumvol in self._extract_f5(d):','self.db.add_tick(symbol,price,qty,cumvol,now)']:
    hits=[(i+1,l.strip()) for i,l in enumerate(src.splitlines()) if needle in l]
    print('CODE_HITS',needle,hits)

con=sqlite3.connect(DB)
cur=con.cursor()
for sym in ['AMD','PLTR','QQQ','NVDA','INTC']:
    try:
        row=cur.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
        print('LATEST_TICK',sym,row)
    except Exception as e:
        print('LATEST_TICK_ERROR',sym,repr(e))

try:
    rows=cur.execute('select payload,ts from raw_ws order by id desc limit 2000').fetchall()
except Exception as e:
    print('RAW_READ_ERROR',repr(e)); rows=[]
shape=Counter(); types=Counter(); items=Counter()
for payload,ts in rows:
    try: d=json.loads(payload)
    except Exception: continue
    if isinstance(d,dict):
        shape[str(d.get('trnm') or 'NO_TRNM')]+=1
        for r in d.get('data') or []:
            if isinstance(r,dict):
                types[str(r.get('type'))]+=1
                items[str(r.get('item'))]+=1
print('RAW_TRNM_COUNTS=',dict(shape))
print('RAW_DATA_TYPE_COUNTS=',dict(types))
print('RAW_TOP_ITEMS=',items.most_common(30))
con.close()

print('=== JOURNAL WS LINES LAST 20 MIN ===')
cmd="journalctl -u day-trader-api.service --since '20 min ago' --no-pager | grep -Ei 'WebSocket|REG|F5|reconnect|live:' | tail -120"
subprocess.run(cmd,shell=True)
print('NEXT=DETERMINE_WHETHER_RAW_WS_STORAGE_IS_SKIPPING_DATA_OR_F5_MESSAGES_NEVER_REACH_MAIN_LOOP')
