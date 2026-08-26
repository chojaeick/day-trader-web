#!/usr/bin/env python3
"""V157 restart and verify DAYTRADE persistence + frozen USA runtime.

Operational verification only. No strategy changes. No broker/order changes.
Restarts the service, waits for HTTP readiness, then checks:
- runtime mode remains DAYTRADE after restart
- USA status/finder/tracker respond
- tracker cached endpoint stays fast
- frozen eval remains visible
- DB remains responsive
"""
from __future__ import annotations
import json, subprocess, time, urllib.request, urllib.error, sqlite3
from pathlib import Path

BASE='http://127.0.0.1:8000'
DB=Path('/home/ubuntu/day-trader-api/daytrader.db')

def get(path,timeout=3):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            try: data=json.loads(raw)
            except Exception: data={'_raw':raw}
            return r.status, round(time.time()-t,3), None, data
    except Exception as e:
        return None, round(time.time()-t,3), str(e), None

def count_key(obj,key):
    if isinstance(obj,dict):
        return (1 if key in obj else 0)+sum(count_key(v,key) for v in obj.values())
    if isinstance(obj,list):
        return sum(count_key(v,key) for v in obj)
    return 0

print('=== V157 RESTART + VERIFY DAYTRADE + FROZEN RUNTIME ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')
p=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('SYSTEMCTL_RESTART_RC=',p.returncode)

ready=False
for i in range(1,31):
    code,sec,err,data=get('/api/v4/runtime-mode',timeout=2)
    print('READY_PROBE',i,'HTTP=',code,'SEC=',sec,'ERR=',err)
    if code==200:
        ready=True
        break
    time.sleep(1)
print('API_READY=',ready)

mode_daytrade=False
mode_data=None
if ready:
    code,sec,err,mode_data=get('/api/v4/runtime-mode',timeout=3)
    print('RUNTIME_MODE_HTTP=',code,'SEC=',sec,'ERR=',err)
    print('RUNTIME_MODE=',mode_data)
    mode_daytrade=bool(isinstance(mode_data,dict) and mode_data.get('mode')=='DAYTRADE')
print('MODE_DAYTRADE=',mode_daytrade)

frozen_eval=0
frozen_ctx=0
frozen_errors=0
tracker_samples=[]
endpoint_ok=True
for path in ['/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker']:
    code,sec,err,data=get(path,timeout=5)
    print(path,'HTTP=',code,'SEC=',sec,'ERR=',err)
    endpoint_ok &= (code==200)
    if path.endswith('/tracker') and code==200: tracker_samples.append(sec)
    if data is not None:
        frozen_eval += count_key(data,'williams_frozen_eval')
        frozen_ctx += count_key(data,'williams_frozen_ctx')
        frozen_errors += count_key(data,'williams_frozen_error')

for i in range(3):
    code,sec,err,data=get('/api/v4/USA/tracker',timeout=5)
    print('TRACKER_REPEAT',i+1,'HTTP=',code,'SEC=',sec,'ERR=',err)
    endpoint_ok &= (code==200)
    if code==200: tracker_samples.append(sec)
    if data is not None:
        frozen_eval += count_key(data,'williams_frozen_eval')
        frozen_ctx += count_key(data,'williams_frozen_ctx')
        frozen_errors += count_key(data,'williams_frozen_error')

tracker_fast=bool(tracker_samples) and max(tracker_samples)<1.0
print('TRACKER_SAMPLES=',tracker_samples)
print('TRACKER_FAST=',tracker_fast)
print('FROZEN_EVAL_HITS=',frozen_eval)
print('FROZEN_CTX_HITS=',frozen_ctx)
print('FROZEN_ERRORS=',frozen_errors)
print('FROZEN_RUNTIME_VISIBLE=',frozen_eval>0 or frozen_ctx>0)

try:
    t=time.time(); con=sqlite3.connect(str(DB),timeout=3); cur=con.cursor(); cur.execute('select count(*) from ranking_snapshots'); n=cur.fetchone()[0]; con.close(); dbsec=round(time.time()-t,3); dbok=True
    print('DB_OK=True SEC=',dbsec,'RANKING_SNAPSHOTS=',n)
except Exception as e:
    dbok=False; dbsec=999
    print('DB_OK=False ERR=',e)

try:
    s=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=3).stdout.strip()
except Exception as e:
    s='UNKNOWN:'+str(e)
print('SERVICE_STATE=',s)

ok=bool(ready and mode_daytrade and endpoint_ok and tracker_fast and frozen_errors==0 and dbok and dbsec<1.0 and s=='active')
print('V157_PASS=',ok)
print('NEXT=' + ('CONTINUE_LIVE_USA_FROZEN_DIAG_AND_PAPER_EVENT_OBSERVATION' if ok else 'FIX_ONLY_FAILED_RUNTIME_ITEM; NO_STRATEGY_CHANGE'))
