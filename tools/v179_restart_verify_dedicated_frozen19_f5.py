#!/usr/bin/env python3
import subprocess,time,sqlite3,json,urllib.request
from datetime import datetime,timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
URL='http://127.0.0.1:8000'
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V179 RESTART + VERIFY DEDICATED FROZEN19 F5 ===')
print('SERVICE_MUTATION=RESTART_ONLY STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')

def get_last(con,s):
    r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(s,)).fetchone()
    return dict(r) if r else None

r=subprocess.run(['sudo','systemctl','restart','day-trader-api'],capture_output=True,text=True,timeout=40)
print('RESTART_RC=',r.returncode)
ready=False
for i in range(1,41):
    try:
        with urllib.request.urlopen(URL+'/api/v4/USA/status',timeout=2) as x:
            if x.status==200:
                print('API_READY_PROBE=',i); ready=True; break
    except Exception: pass
    time.sleep(1)
print('API_READY=',ready)
if not ready:
    raise SystemExit(2)

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
base={s:get_last(con,s) for s in FROZEN}
print('BASELINE_UTC=',datetime.now(timezone.utc).isoformat())
for s in FROZEN:
    print('BASE',s,base[s])

for sec in (15,30,45,60,75,90):
    time.sleep(15)
    cur={s:get_last(con,s) for s in FROZEN}
    new=[]
    for s in FROZEN:
        b=base[s]; c=cur[s]
        if c and (not b or int(c['id'])>int(b['id'])): new.append(s)
    print('OBSERVE_SEC=',sec,'NEW_TICK_SYMBOLS=',len(new),new)

now=datetime.now(timezone.utc)
fresh=[];stale=[];missing=[]
for s in FROZEN:
    x=get_last(con,s)
    if not x:
        missing.append(s); print('SYMBOL',s,'MISSING'); continue
    try:
        dt=datetime.fromisoformat(str(x['ts']).replace('Z','+00:00')); age=(now-dt).total_seconds()
    except Exception: age=999999999
    cls='FRESH' if age<180 else 'STALE'
    (fresh if cls=='FRESH' else stale).append(s)
    print('SYMBOL',s,'LAST=',x['ts'],'AGE_SEC=',round(age,1),'CLASS=',cls)
con.close()
print('FRESH_COUNT=',len(fresh),fresh)
print('STALE_COUNT=',len(stale),stale)
print('MISSING_COUNT=',len(missing),missing)

j=subprocess.run(['journalctl','-u','day-trader-api','--since','3 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
print('=== RECENT DEDICATED FROZEN19 WS LOGS ===')
for line in j.stdout.splitlines():
    low=line.lower()
    if any(k in low for k in ['frozen19','websocket','reg','error','failed','exception']): print(line[-1400:])

print('V179_DEDICATED_FROZEN19_F5_PASS=',len(fresh)>=15)
print('NEXT=IF_PASS_VERIFY_COMPLETED_1M_CTX_SINGLE_EVAL; IF_FAIL_DIAGNOSE_DEDICATED_WS_REGISTRATION/PARSE_PATH_ONLY')
