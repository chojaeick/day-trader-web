#!/usr/bin/env python3
import re, subprocess, sqlite3
from pathlib import Path
from datetime import datetime, timezone

print('=== V182 F5 ITEM FORMAT + SESSION BEHAVIOR AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

p=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
text=p.read_text(encoding='utf-8')
lines=text.splitlines()

for pat in ['def _ws_items','def _extract_f5','def websocket_forever','stex_tp','jmcode','type']:
    hits=[i for i,l in enumerate(lines,1) if pat in l]
    print('PATTERN',pat,'HITS=',hits)
    for i in hits[:12]:
        a=max(1,i-8); b=min(len(lines),i+24)
        print(f'--- CONTEXT {a}:{b} ---')
        for n in range(a,b+1): print(f'{n}: {lines[n-1]}')

# inspect recent raw websocket payload shapes; do not assume symbol text appears verbatim
con=sqlite3.connect('/home/ubuntu/day-trader-api/daytrader.db')
con.row_factory=sqlite3.Row
try:
    rows=con.execute('select id,payload,ts from raw_ws order by id desc limit 120').fetchall()
except Exception as e:
    print('RAW_WS_ERROR=',e); rows=[]
print('RAW_WS_SAMPLE_COUNT=',len(rows))
shape_counts={}
for r in rows:
    s=str(r['payload'] or '')
    key='REG' if '"trnm":"REG"' in s.replace(' ','') else ('PING' if 'PING' in s else ('F5' if 'F5' in s else 'OTHER'))
    shape_counts[key]=shape_counts.get(key,0)+1
print('RAW_WS_SHAPE_COUNTS=',shape_counts)
for r in rows[:30]:
    s=str(r['payload'] or '')
    if 'REG' not in s and 'PING' not in s:
        print('RAW_NONREG=',{'id':r['id'],'ts':r['ts'],'payload':s[:1200]})

# compare fresh/stale set with quote/exchange and last tick
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
now=datetime.now(timezone.utc)
for sym in FROZEN:
    q=con.execute('select exchange,updated_at,price from quotes where symbol=? order by updated_at desc limit 1',(sym,)).fetchone()
    t=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    age=None
    if t:
        try: age=(now-datetime.fromisoformat(str(t['ts']).replace('Z','+00:00'))).total_seconds()
        except Exception: pass
    print('SYM',sym,'QUOTE=',dict(q) if q else None,'TICK=',dict(t) if t else None,'AGE_SEC=',None if age is None else round(age,1))
con.close()

# current service logs for websocket register/close/errors
r=subprocess.run(['journalctl','-u','day-trader-api','--since','20 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
print('=== RECENT WS LOGS ===')
for line in r.stdout.splitlines():
    low=line.lower()
    if any(k in low for k in ['websocket','f5','reg','bye','close','reconnect','return_code','error']):
        print(line[-1400:])

print('NEXT=IF_RAW_F5_SHAPE_REVEALS_SYMBOL_ENCODING_MISMATCH_PATCH__extract_f5_OR_ws_items; IF_NO_F5_FOR_STALE_BUT_REG_ACK_OK_TEST_SINGLE_WS_FROZEN19_ONLY_WITH_DYNAMIC_REMOVED; STRATEGY_UNCHANGED')
