#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, sqlite3, json

ROOT=Path('/home/ubuntu/day-trader-api')
print('=== V174 FROZEN19 WS REGISTRATION + F5 DELIVERY AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

core=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']

# Inspect relevant runtime source only; no imports required.
for fn in ['live_server/kiwoom.py','live_server/config.py']:
    p=ROOT/fn
    print('\nFILE',p,'EXISTS=',p.exists())
    if not p.exists(): continue
    lines=p.read_text(errors='ignore').splitlines()
    pats=['def _ws_items','def active_exchange','stex_tp','exchange','F5']
    hits=[]
    for i,l in enumerate(lines,1):
        if any(x in l for x in pats): hits.append(i)
    print('HITS=',hits[:80])
    for h in hits[:12]:
        a=max(1,h-8); b=min(len(lines),h+18)
        print(f'--- CONTEXT {a}:{b} ---')
        for n in range(a,b+1): print(f'{n}: {lines[n-1]}')

# DB quote/tick evidence.
db='/home/ubuntu/day-trader-api/daytrader.db'
con=sqlite3.connect(db); con.row_factory=sqlite3.Row
for sym in core:
    q=None
    try:
        q=con.execute('select * from quotes where symbol=? order by updated_at desc limit 1',(sym,)).fetchone()
    except Exception:
        try:q=con.execute('select * from quote_snapshots where symbol=? order by updated_at desc limit 1',(sym,)).fetchone()
        except Exception:pass
    t=None
    try:t=con.execute('select ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    except Exception:pass
    print('SYMBOL',sym,'QUOTE_EXCHANGE=',(dict(q).get('exchange') if q else None),'QUOTE_UPDATED=',(dict(q).get('updated_at') if q else None),'LAST_TICK=',(dict(t) if t else None))

# Inspect recent raw websocket payloads for any frozen symbols; bounded sample.
print('\n=== RAW_WS RECENT SYMBOL PRESENCE ===')
try:
    rows=con.execute('select * from raw_ws order by rowid desc limit 500').fetchall()
except Exception:
    rows=[]
for sym in core:
    cnt=0
    newest=None
    for r in rows:
        d=dict(r)
        blob=' '.join(str(v) for v in d.values() if v is not None)
        if sym in blob:
            cnt+=1
            if newest is None:newest=d
    print('RAW',sym,'HITS_LAST500=',cnt,'NEWEST_HINT=',newest)
con.close()

# Recent registration logs.
print('\n=== RECENT WS LOGS ===')
try:
    out=subprocess.run(['journalctl','-u','day-trader-api','--since','15 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20).stdout
    for line in out.splitlines():
        if 'WebSocket universe refreshed' in line or 'websocket' in line.lower() and ('error' in line.lower() or 'warn' in line.lower()):
            print(line)
except Exception as e: print('JOURNAL_ERR',repr(e))

print('NEXT=IF_RAW_ABSENT_BUT_REGISTRATION_PRESENT_DIAGNOSE_KIWOOM_SUBSCRIPTION_LIMIT_OR_ITEM_FORMAT; IF_RAW_PRESENT_BUT_TICKS_STALE_DIAGNOSE_EXTRACT_F5_MAPPING')
