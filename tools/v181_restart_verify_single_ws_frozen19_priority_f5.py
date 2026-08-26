#!/usr/bin/env python3
import subprocess, sqlite3, time, urllib.request, json
from datetime import datetime, timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V181 RESTART + VERIFY SINGLE WS FROZEN19 PRIORITY F5 ===')
print('SERVICE_MUTATION=RESTART_ONLY STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')

rc=subprocess.run(['sudo','systemctl','restart','day-trader-api.service']).returncode
print('RESTART_RC=',rc)
ready=False
for i in range(1,41):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as r:
            if r.status==200:
                ready=True; print('API_READY_PROBE=',i); break
    except Exception:
        pass
    time.sleep(1)
print('API_READY=',ready)

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
base={}
for s in FROZEN:
    r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    base[s]=dict(r) if r else None
print('BASELINE_UTC=',datetime.now(timezone.utc).isoformat())
for s in FROZEN: print('BASE',s,base[s])

for sec in (15,30,45,60,75,90):
    time.sleep(15)
    new=[]
    for s in FROZEN:
        r=con.execute('select id,ts from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
        if r and (base[s] is None or int(r['id'])>int(base[s]['id'])): new.append(s)
    print('OBSERVE_SEC=',sec,'NEW_TICK_SYMBOLS=',len(new),new)

now=datetime.now(timezone.utc); fresh=[]; stale=[]
for s in FROZEN:
    r=con.execute('select ts,price,qty from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    if not r:
        stale.append(s); print('SYMBOL',s,'MISSING'); continue
    try:
        dt=datetime.fromisoformat(str(r['ts']).replace('Z','+00:00')); age=(now-dt).total_seconds()
    except Exception: age=10**9
    cls='FRESH' if age<180 else 'STALE'
    (fresh if cls=='FRESH' else stale).append(s)
    print('SYMBOL',s,'LAST=',r['ts'],'AGE_SEC=',round(age,1),'CLASS=',cls)
print('FRESH_COUNT=',len(fresh),fresh)
print('STALE_COUNT=',len(stale),stale)
con.close()

# Verify only one USA websocket task is started and frozen symbols are first in runtime registration logs.
r=subprocess.run(['journalctl','-u','day-trader-api','--since','4 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
lines=r.stdout.splitlines()
print('=== RECENT WS LOGS ===')
for line in lines:
    if 'WebSocket live:' in line or 'WebSocket universe refreshed:' in line or 'Frozen19 WebSocket' in line or 'websocket reconnect' in line.lower():
        print(line[-1600:])
regs=[x for x in lines if 'WebSocket live:' in x or 'WebSocket universe refreshed:' in x]
latest=regs[-1] if regs else ''
first19=[]
if latest:
    tail=latest.split(': ',1)[-1]
    # safer split from marker
    if 'WebSocket live:' in latest: tail=latest.split('WebSocket live:',1)[1].strip()
    elif 'WebSocket universe refreshed:' in latest: tail=latest.split('WebSocket universe refreshed:',1)[1].strip()
    first19=[x.split('/')[0] for x in tail.split(',')[:19]]
print('LATEST_FIRST19=',first19)
print('FROZEN_FIRST19_EXACT=',first19==FROZEN)
second_ws=any('Frozen19 WebSocket live:' in x for x in lines)
print('SECOND_FROZEN_WS_SEEN=',second_ws)
pass_flag=(not second_ws) and len(fresh)>=15
print('V181_SINGLE_WS_FROZEN19_PRIORITY_PASS=',pass_flag)
print('NEXT=IF_PASS_VERIFY_COMPLETED_1M_CTX_SINGLE_EVAL; IF_FAIL_USE_STALE_SET_TO_DIAGNOSE_F5_ITEM_FORMAT/SESSION_BEHAVIOR_ONLY')
