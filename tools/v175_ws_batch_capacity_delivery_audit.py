#!/usr/bin/env python3
import subprocess, sqlite3, re, time
from datetime import datetime, timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
FROZEN=['SOXL','SOXS','TQQQ','SQQQ','QQQ','SPY','SMH','PLTR','AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM']
print('=== V175 WS BATCH CAPACITY + DELIVERY AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

# inspect exact websocket registration implementation and batching/limits
p='/home/ubuntu/day-trader-api/live_server/kiwoom.py'
text=open(p,encoding='utf-8').read().splitlines()
keys=('websocket','ws_items','WebSocket universe refreshed','REG','registration','jmcode','stex_tp','items','type')
hits=[]
for i,line in enumerate(text,1):
    low=line.lower()
    if any(k.lower() in low for k in keys): hits.append(i)
print('KIWOOM_WS_HITS=',hits[-80:])
for i in hits[-30:]:
    a=max(1,i-3); b=min(len(text),i+5)
    print(f'--- {a}:{b} ---')
    for n in range(a,b+1): print(f'{n}: {text[n-1]}')

# tick freshness grouped by legacy-first vs augmented frozen symbols
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
now=datetime.now(timezone.utc)
print('NOW_UTC=',now.isoformat())
for s in FROZEN:
    r=con.execute('select ts,price,qty from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    if not r:
        print('TICK',s,'MISSING'); continue
    try:
        dt=datetime.fromisoformat(str(r['ts']).replace('Z','+00:00')); age=(now-dt).total_seconds()
    except Exception: age=None
    print('TICK',s,'LAST=',r['ts'],'AGE_SEC=',None if age is None else round(age,1),'FRESH=',bool(age is not None and age<180))

# recent registration and websocket warnings/errors, including server acknowledgements
cmd=['journalctl','-u','day-trader-api','--since','25 minutes ago','--no-pager']
r=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
lines=r.stdout.splitlines()
print('=== RECENT WS REG/ACK/ERROR LOGS ===')
for line in lines:
    low=line.lower()
    if any(x in low for x in ['websocket','socket','subscribe','register','universe refreshed','return_code','error','failed','disconnect','close']):
        print(line[-1200:])

# quantify latest registration list order/size from log
regs=[x for x in lines if 'WebSocket universe refreshed:' in x]
if regs:
    last=regs[-1]
    tail=last.split('WebSocket universe refreshed:',1)[1].strip()
    items=[x.strip() for x in tail.split(',') if x.strip()]
    print('LATEST_REG_COUNT=',len(items))
    print('LATEST_REG_ITEMS=',items)
    pos={x.split('/')[0]:i+1 for i,x in enumerate(items)}
    print('FROZEN_REG_POSITIONS=',{s:pos.get(s) for s in FROZEN})
    fresh=[]; stale=[]
    for s in FROZEN:
        rr=con.execute('select ts from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
        if not rr: stale.append(s); continue
        try:
            dt=datetime.fromisoformat(str(rr['ts']).replace('Z','+00:00'))
            (fresh if (now-dt).total_seconds()<180 else stale).append(s)
        except Exception: stale.append(s)
    print('FRESH=',fresh)
    print('STALE=',stale)
    print('FRESH_POSITIONS=',{s:pos.get(s) for s in fresh})
    print('STALE_POSITIONS=',{s:pos.get(s) for s in stale})
else:
    print('LATEST_REG_COUNT=NONE')
con.close()
print('NEXT=IF_FRESH_CLUSTER_IS_EARLY_REGISTRATION_POSITIONS_PATCH_WS_BATCHING/SUBSCRIPTION_CAPACITY_ONLY; IF_NOT_INSPECT_ACK_AND_ITEM_FORMAT; STRATEGY_UNCHANGED')
