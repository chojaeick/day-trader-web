#!/usr/bin/env python3
"""V153 runtime bottleneck diagnosis.
READ ONLY: no strategy changes, no service mutation, no orders.
Diagnoses repeated /api/v4/USA/tracker timeout and sparse frozen telemetry.
"""
from __future__ import annotations
import json, os, re, sqlite3, subprocess, time, urllib.request
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
API=ROOT/'live_server'/'api.py'
ENG=ROOT/'live_server'/'v4_engine.py'
BASE='http://127.0.0.1:8000'

def sh(cmd,timeout=8):
    try:
        p=subprocess.run(cmd,shell=True,text=True,capture_output=True,timeout=timeout)
        return p.returncode,(p.stdout+p.stderr).strip()
    except Exception as e:return -1,str(e)

def get(path,timeout):
    t=time.monotonic()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read(); code=r.status
        return code,time.monotonic()-t,json.loads(raw.decode()) if raw else None,None
    except Exception as e:return None,time.monotonic()-t,None,str(e)

def rows_of(d):
    if not isinstance(d,dict):return []
    for k in ('rows','tracker','finder'):
        v=d.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict) and isinstance(v.get('rows'),list): return v['rows']
    return []

def main():
    print('=== V153 RUNTIME BOTTLENECK DIAGNOSIS ===')
    print('READ_ONLY=YES SERVICE_MUTATION=NONE ORDERS=NONE STRATEGY_CHANGE=NONE')
    rc,state=sh('systemctl is-active day-trader-api.service')
    print('SERVICE_STATE=',state)
    rc,ps=sh("ps -eo pid,ppid,pcpu,pmem,etime,stat,cmd --sort=-pcpu | head -12")
    print('=== PROCESS TOP ===');print(ps)

    # Endpoint timing with both short and long deadlines.
    endpoint_results=[]
    for p in ('/api/v4/runtime-mode','/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker'):
        code,sec,d,err=get(p,3)
        print('HTTP3',p,'code=',code,'sec=',round(sec,3),'err=',err)
        if isinstance(d,dict):
            rr=rows_of(d); print(' rows=',len(rr),'session=',d.get('session'),'market=',d.get('market'),'updated=',(d.get('tracker') or {}).get('updated_at') if isinstance(d.get('tracker'),dict) else None)
        endpoint_results.append((p,code,sec,err))
    code,sec,d,err=get('/api/v4/USA/tracker',15)
    print('HTTP15 /api/v4/USA/tracker code=',code,'sec=',round(sec,3),'err=',err,'rows=',len(rows_of(d)))

    # Source route/function evidence from deployed runtime, not GitHub assumptions.
    api=API.read_text(errors='ignore') if API.exists() else ''
    eng=ENG.read_text(errors='ignore') if ENG.exists() else ''
    print('=== TRACKER ROUTE SOURCE HITS ===')
    for i,line in enumerate(api.splitlines(),1):
        low=line.lower()
        if ('tracker' in low and ('@app.' in low or 'def ' in low or 'refresh' in low or 'to_thread' in low)) or 'v4_engine_forever' in low:
            print(i,line[:240])
    print('=== ENGINE REFRESH SOURCE HITS ===')
    for i,line in enumerate(eng.splitlines(),1):
        low=line.lower()
        if ('refresh' in low and 'tracker' in low) or ('williams_frozen' in low) or ('_paper_williams_step' in low):
            print(i,line[:240])

    # Thread/process count and live fd pressure.
    rc,pids=sh("pgrep -f 'uvicorn live_server.api:app' | head -1")
    pid=(pids.splitlines()[0].strip() if pids else '')
    print('UVICORN_PID=',pid)
    if pid.isdigit():
        for label,cmd in [
            ('THREADS',f"ls /proc/{pid}/task 2>/dev/null | wc -l"),
            ('FDS',f"ls /proc/{pid}/fd 2>/dev/null | wc -l"),
            ('STATUS',f"grep -E 'State|Threads|VmRSS|voluntary_ctxt_switches|nonvoluntary_ctxt_switches' /proc/{pid}/status 2>/dev/null"),
        ]:
            rc,out=sh(cmd);print(label,out)

    # DB health / lock latency.
    db=ROOT/'daytrader.db'
    try:
        t=time.monotonic();c=sqlite3.connect(str(db),timeout=2)
        c.execute('select 1').fetchone()
        n=c.execute("select count(*) from v4_tracker_snapshots where market='USA'").fetchone()[0]
        last=c.execute("select max(ts) from v4_tracker_snapshots where market='USA'").fetchone()[0]
        c.close();print('DB_OK=True sec=',round(time.monotonic()-t,3),'USA_SNAPSHOTS=',n,'LAST=',last)
    except Exception as e:print('DB_OK=False error=',repr(e))

    # Last logs, focused on loop failures and long/invalid symbols.
    rc,j=sh("sudo journalctl -u day-trader-api.service --since '10 min ago' --no-pager | grep -E 'V4 engine loop failed|Traceback|ERROR|snapshot|tracker|ADBT|WILLIAMS|timed out' | tail -120",timeout=10)
    print('=== JOURNAL FOCUSED ===');print(j or '<none>')

    tracker_short=next((x for x in endpoint_results if x[0].endswith('/tracker')),None)
    tracker_timeout=bool(tracker_short and tracker_short[1] is None)
    cpu_hot=False
    m=re.search(r'^\s*(\d+)\s+\d+\s+([0-9.]+).*uvicorn live_server\.api:app',ps,re.M)
    if m: cpu_hot=float(m.group(2))>=70.0
    print('=== DIAGNOSIS FLAGS ===')
    print('TRACKER_SHORT_TIMEOUT=',tracker_timeout)
    print('UVICORN_CPU_HOT=',cpu_hot)
    print('STATUS_RESPONDS=',any(p.endswith('/status') and code==200 for p,code,_,_ in endpoint_results))
    print('FINDER_RESPONDS=',any(p.endswith('/finder') and code==200 for p,code,_,_ in endpoint_results))
    if tracker_timeout and cpu_hot:
        print('PRIMARY_SUSPECT=TRACKER_REFRESH_CPU_OR_BLOCKING_WORK_ON_REQUEST_PATH')
    elif tracker_timeout:
        print('PRIMARY_SUSPECT=TRACKER_REQUEST_PATH_BLOCKING_OR_LOCK_CONTENTION')
    else:
        print('PRIMARY_SUSPECT=NO_CURRENT_TRACKER_TIMEOUT_REPRODUCED')
    print('NEXT=USE_THIS_OUTPUT_TO_PATCH_ONLY_RUNTIME_BOTTLENECK; FROZEN_STRATEGY_UNCHANGED')

if __name__=='__main__': main()
