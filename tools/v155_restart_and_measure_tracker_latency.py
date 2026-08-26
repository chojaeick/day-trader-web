#!/usr/bin/env python3
"""V155 restart service and re-measure USA tracker latency after V154C.

Operational only. No strategy or order changes.
"""
from __future__ import annotations
import json, subprocess, time, urllib.request, sqlite3
from pathlib import Path

BASE='http://127.0.0.1:8000'
DB=Path('/home/ubuntu/day-trader-api/daytrader.db')

def req(path, timeout=5):
    t=time.perf_counter()
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            raw=r.read().decode('utf-8','replace')
            code=r.getcode()
        dt=time.perf_counter()-t
        try: data=json.loads(raw)
        except Exception: data=raw[:500]
        return code,dt,None,data
    except Exception as e:
        return None,time.perf_counter()-t,str(e),None

print('=== V155 RESTART + TRACKER LATENCY AUDIT ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')

p=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('SYSTEMCTL_RESTART_RC=',p.returncode)
if p.stdout.strip(): print(p.stdout.strip())
if p.stderr.strip(): print(p.stderr.strip())
time.sleep(3)

p=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True)
state=(p.stdout or p.stderr).strip()
print('SERVICE_STATE=',state)

for path in ['/api/v4/runtime-mode','/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker']:
    code,dt,err,data=req(path,5)
    print(path,'HTTP=',code,'SEC=',round(dt,3),'ERR=',err)
    if path.endswith('/tracker') and isinstance(data,dict):
        print('TRACKER_KEYS=',sorted(list(data.keys()))[:20])
        rows=data.get('rows') or data.get('items') or data.get('tracker') or []
        print('TRACKER_ROWS=',len(rows) if isinstance(rows,list) else 'NA')

# second/third tracker call should also be fast if request path is cached
tracker_times=[]
for i in range(3):
    code,dt,err,data=req('/api/v4/USA/tracker',5)
    tracker_times.append(dt)
    print('TRACKER_REPEAT',i+1,'HTTP=',code,'SEC=',round(dt,3),'ERR=',err)

# process snapshot
p=subprocess.run("ps -eo pid,ppid,%cpu,%mem,etime,stat,cmd --sort=-%cpu | head -8",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ===')
print(p.stdout.strip())

# lightweight DB latency
try:
    t=time.perf_counter()
    con=sqlite3.connect(str(DB),timeout=3)
    cur=con.cursor(); cur.execute('select count(*) from ranking_snapshots'); n=cur.fetchone()[0]
    con.close(); dbsec=time.perf_counter()-t
    print('DB_OK=True SEC=',round(dbsec,3),'RANKING_SNAPSHOTS=',n)
except Exception as e:
    dbsec=999.0; print('DB_OK=False ERR=',e)

# runtime mode may reset to NORMAL on restart; report explicitly
code,dt,err,mode=req('/api/v4/runtime-mode',5)
mode_name=mode.get('mode') if isinstance(mode,dict) else None
print('RUNTIME_MODE=',mode_name)

avg=sum(tracker_times)/len(tracker_times) if tracker_times else 999
tracker_fast=all(x < 2.0 for x in tracker_times)
print('TRACKER_AVG_SEC=',round(avg,3))
print('TRACKER_FAST=',tracker_fast)
print('DB_RESPONSIVE=',dbsec < 2.0)
print('V155_PASS=',bool(state=='active' and tracker_fast))
print('NEXT=' + ('IF_MODE_NORMAL_SET_DAYTRADE_THEN_RECHECK; ELSE_CONTINUE_LIVE_DIAG' if state=='active' and tracker_fast else 'DIAGNOSE_BACKGROUND_REFRESH_CPU_OR_CACHE_SHAPE'))
