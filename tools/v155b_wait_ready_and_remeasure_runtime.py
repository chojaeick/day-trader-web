#!/usr/bin/env python3
"""V155B wait for API readiness then remeasure tracker/runtime health.

No strategy/order changes. Service is already restarted by V155.
This script waits for port/API readiness before judging latency.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, time
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

BASE='http://127.0.0.1:8000'
DB='/home/ubuntu/day-trader-api/daytrader.db'

print('=== V155B WAIT READY + REMEASURE RUNTIME ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

def get(path, timeout=5):
    t=time.time()
    try:
        with urlopen(BASE+path, timeout=timeout) as r:
            raw=r.read().decode('utf-8','replace')
            sec=time.time()-t
            try: data=json.loads(raw)
            except Exception: data=raw
            return r.status,sec,None,data
    except Exception as e:
        return None,time.time()-t,str(e),None

ready=False
for i in range(30):
    code,sec,err,data=get('/api/v4/runtime-mode',2)
    print('READY_PROBE',i+1,'HTTP=',code,'SEC=',round(sec,3),'ERR=',err)
    if code==200:
        ready=True
        break
    time.sleep(1)

print('API_READY=',ready)
if not ready:
    try:
        j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True,timeout=10)
        print('=== JOURNAL TAIL ===')
        print(j.stdout[-12000:])
    except Exception as e: print('JOURNAL_ERROR=',e)
    print('V155B_PASS=False')
    print('NEXT=DIAGNOSE_STARTUP_FAILURE')
    raise SystemExit(2)

# runtime mode
code,sec,err,mode=get('/api/v4/runtime-mode',5)
print('RUNTIME_MODE_HTTP=',code,'SEC=',round(sec,3),'ERR=',err)
print('RUNTIME_MODE=',mode)
mode_daytrade=isinstance(mode,dict) and str(mode.get('mode')).upper()=='DAYTRADE'
print('MODE_DAYTRADE=',mode_daytrade)

# endpoint latency after readiness
samples=[]
for path in ['/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker']:
    code,sec,err,data=get(path,10)
    print(path,'HTTP=',code,'SEC=',round(sec,3),'ERR=',err)
    if path.endswith('/tracker') and code==200: samples.append(sec)

for i in range(3):
    code,sec,err,data=get('/api/v4/USA/tracker',5)
    print('TRACKER_REPEAT',i+1,'HTTP=',code,'SEC=',round(sec,3),'ERR=',err)
    if code==200: samples.append(sec)
    time.sleep(.5)

tracker_ok=(len(samples)>=3 and max(samples)<2.0)
print('TRACKER_SAMPLES=',[round(x,3) for x in samples])
print('TRACKER_FAST=',tracker_ok)

# CPU/memory
try:
    p=subprocess.run(['bash','-lc',"ps -eo pid,ppid,%cpu,%mem,etime,stat,cmd --sort=-%cpu | head -12"],capture_output=True,text=True,timeout=5)
    print('=== PROCESS TOP ===')
    print(p.stdout.strip())
except Exception as e: print('PROCESS_TOP_ERROR=',e)

# DB latency
try:
    t=time.time(); con=sqlite3.connect(DB,timeout=3); cur=con.cursor(); cur.execute('select count(*) from ranking_snapshots'); n=cur.fetchone()[0]; con.close(); dbsec=time.time()-t
    print('DB_OK=True SEC=',round(dbsec,3),'RANKING_SNAPSHOTS=',n)
    db_ok=dbsec<1.0
except Exception as e:
    print('DB_OK=False ERR=',e); db_ok=False

# Service state
try:
    s=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
    state=(s.stdout or s.stderr).strip()
except Exception as e: state='UNKNOWN:'+str(e)
print('SERVICE_STATE=',state)

passed=ready and state=='active' and tracker_ok and db_ok
print('V155B_PASS=',passed)
if not mode_daytrade:
    print('BLOCKER=RUNTIME_MODE_RESET_TO_NORMAL_AFTER_RESTART')
    print('NEXT=SET_DAYTRADE_AGAIN_THEN_CONTINUE_RUNTIME_DIAG')
elif passed:
    print('NEXT=CONTINUE_LIVE_RUNTIME_DIAG; VERIFY_FROZEN_EVAL_AND_PAPER_LEDGER')
else:
    print('NEXT=CONTINUE_BOTTLENECK_DIAG; DO_NOT_TOUCH_STRATEGY')
