#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, sqlite3, urllib.request, json
from datetime import datetime, timezone

KI=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
BK=KI.with_name('kiwoom.py.bak_v176')
DB='/home/ubuntu/day-trader-api/daytrader.db'
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V176 WS FULL SNAPSHOT REPLACE + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
text=KI.read_text(encoding='utf-8')
shutil.copy2(KI,BK)
# Exact runtime pattern: a full current universe is sent as one REG snapshot.
old="await ws.send(json.dumps({'trnm':'REG','grp_no':'1','refresh':'1','data':[{'item':reg_items,'type':['F5']}]}))"
new="await ws.send(json.dumps({'trnm':'REG','grp_no':'1','refresh':'0','data':[{'item':reg_items,'type':['F5']}]}))"
count=text.count(old)
if count<1:
    print('EXACT_FULL_SNAPSHOT_REG_NOT_FOUND')
    raise SystemExit(2)
text=text.replace(old,new)
KI.write_text(text,encoding='utf-8')
print('PATCHED',KI)
print('BACKUP',BK)
print('FULL_SNAPSHOT_REG_OCCURRENCES_PATCHED=',count)
print('REG_REFRESH_MODE=0_REPLACE_EXISTING')
print('WS_SESSION_LIMIT_ASSUMED_CHANGE=NONE')

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(KI)],capture_output=True,text=True)
print('PY_COMPILE=', 'PASS' if r.returncode==0 else 'FAIL', (r.stderr or '')[-500:])
if r.returncode!=0: raise SystemExit(3)

rr=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',rr.returncode)
# wait API ready
ready=False
for i in range(1,41):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as resp:
            if resp.status==200:
                ready=True; print('API_READY_PROBE=',i); break
    except Exception:
        time.sleep(1)
print('API_READY=',ready)

# Baseline last tick ids/timestamps after restart settles
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
def latest(sym):
    x=con.execute('select id,ts,price from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    return dict(x) if x else None
base={s:latest(s) for s in FROZEN}
print('BASELINE_CAPTURED=',datetime.now(timezone.utc).isoformat())
# observe 75 sec, enough for active premarket symbols
for sec in (15,30,45,60,75):
    time.sleep(15)
    changed=[]
    for s in FROZEN:
        now=latest(s); oldr=base.get(s)
        if now and (not oldr or now['id']!=oldr['id']): changed.append(s)
    print('OBSERVE_SEC=',sec,'NEW_TICK_SYMBOLS=',len(changed),changed)

nowdt=datetime.now(timezone.utc)
fresh=[]; stale=[]
for s in FROZEN:
    x=latest(s)
    age=None
    if x:
        try: age=(nowdt-datetime.fromisoformat(str(x['ts']).replace('Z','+00:00'))).total_seconds()
        except Exception: pass
    cls='FRESH' if age is not None and age<180 else 'STALE'
    (fresh if cls=='FRESH' else stale).append(s)
    print('SYMBOL',s,'LAST=',None if not x else x['ts'],'AGE_SEC=',None if age is None else round(age,1),'CLASS=',cls)
con.close()
print('FRESH_COUNT=',len(fresh),fresh)
print('STALE_COUNT=',len(stale),stale)

j=subprocess.run(['journalctl','-u','day-trader-api','--since','3 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
print('=== RECENT WS LOGS ===')
for line in j.stdout.splitlines():
    low=line.lower()
    if 'websocket' in low or 'universe refreshed' in low or 'return_code' in low or 'error' in low or 'failed' in low:
        print(line[-1500:])

pass_live=len(fresh)>=8
print('V176_FEED_RECOVERY_PASS=',pass_live)
print('NEXT=IF_RECOVERED_WAIT_FOR_REGULAR_AND_VERIFY_FROZEN19_CTX_SINGLE_EVAL; IF_NOT_RECOVERED_ADD_WS_NO_F5_WATCHDOG_RECONNECT_AND_CAPTURE_REG_ACK')
