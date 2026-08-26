#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, os, signal

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V200 PATCH V4 ENGINE SYNC BAR CALL TO THREAD + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=REMOVE_CONFIRMED_EVENT_LOOP_BLOCKING_TICKS_TO_BARS_CALL')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v200')
shutil.copy2(API,bak); print('BACKUP',bak)
old="bars_now=len(ticks_to_bars(db.ticks(sym,2500),1))"
new="bars_now=await asyncio.to_thread(lambda: len(ticks_to_bars(db.ticks(sym,2500),1)))"
count=src.count(old)
print('TARGET_COUNT=',count)
if count!=1: raise SystemExit('EXPECTED_EXACTLY_ONE_SYNC_BAR_CALL')
src=src.replace(old,new,1)
API.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stdout.strip(): print(r.stdout.strip())
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode: raise SystemExit('COMPILE_FAIL')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
if r.stdout.strip(): print(r.stdout.strip())
if r.stderr.strip(): print(r.stderr.strip())

# Wait up to 150s for a fast runtime-mode response. Startup can take ~30s in this project.
def get(path,timeout=5):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e:
        return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',timeout=5)
    print('READY_PROBE',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        ready=True
        print('RUNTIME_MODE=',body)
        break
    time.sleep(5)
print('API_READY=',ready)
if not ready:
    print('JOURNAL_LAST80')
    j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
    print(j.stdout[-12000:])
    raise SystemExit('API_NOT_READY')

# Recheck latency several times to prove the event loop is no longer starved.
lat=[]
for i in range(5):
    ok,code,sec,body=get('/api/v4/runtime-mode',timeout=5)
    lat.append(sec if ok else 99.0)
    print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    time.sleep(1)

for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,timeout=15)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict):
        if ep.endswith('frozen-paper'):
            rows=body.get('rows') or []
            print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
            ctx=sum(1 for x in rows if x.get('ctx'))
            bars=sum(1 for x in rows if x.get('bar'))
            print('FROZEN_CTX_COUNT=',ctx,'FROZEN_BAR_COUNT=',bars)
            for x in rows:
                print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
        else:
            print('STATUS_SESSION=',body.get('session'),'MODE=',body.get('mode'))
    else:
        print('BODY=',body)

# CPU snapshot after stabilization.
p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -12",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ===')
print(p.stdout)
fast=max(lat)<2.0 if lat else False
print('RUNTIME_MODE_FAST=',fast)
print('V200_PASS=',bool(ready and fast))
print('NEXT=IF_FROZEN_ROWS_19_AND_CTX/BAR_CURRENT__LEAVE_RUNNING_USA_PAPER; ELSE_PATCH_ONLY_REMAINING_CONFIRMED_FROZEN_DEFECT')
