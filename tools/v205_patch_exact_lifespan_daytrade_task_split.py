#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V205 EXACT LIFESPAN DAYTRADE TASK SPLIT ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=START_ONLY_FE_WEBSOCKET_PLUS_FROZEN19_PAPER_IN_DAYTRADE; KEEP_NORMAL_TASKS_UNCHANGED')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v205'); shutil.copy2(API,bak); print('BACKUP',bak)

old="""    tasks.extend([asyncio.create_task(k.websocket_forever()),
    # asyncio.create_task(k.frozen19_websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
    asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
    asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
    asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
    asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
    asyncio.create_task(williams_mock_hard_stop_forever()),
    asyncio.create_task(v4_engine_forever()),
    asyncio.create_task(frozen_usa_paper_forever())])
"""
new="""    if _runtime_profile().get('mode')=='DAYTRADE':
        # V205: frozen USA paper authority only. Keep the FE websocket alive and
        # evaluate frozen19 paper; do not start legacy discovery/recovery/tracker workloads.
        tasks.extend([
            asyncio.create_task(k.websocket_forever()),
            asyncio.create_task(frozen_usa_paper_forever()),
        ])
    else:
        tasks.extend([asyncio.create_task(k.websocket_forever()),
        # asyncio.create_task(k.frozen19_websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
        asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
        asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
        asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
        asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
        asyncio.create_task(williams_mock_hard_stop_forever()),
        asyncio.create_task(v4_engine_forever()),
        asyncio.create_task(frozen_usa_paper_forever())])
"""
count=src.count(old)
print('EXACT_LIFESPAN_BLOCK_COUNT=',count)
if count!=1:
    p=src.find('tasks.extend([asyncio.create_task(k.websocket_forever())')
    print('CONTEXT=',repr(src[max(0,p-300):p+1200] if p>=0 else 'NOT_FOUND'))
    raise SystemExit('EXACT_BLOCK_NOT_FOUND__NO_MUTATION')
src=src.replace(old,new,1)
API.write_text(src)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); print('RESTORED_AFTER_COMPILE_FAIL=YES'); raise SystemExit('COMPILE_FAIL')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,timeout=3):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e: return False,0,time.time()-t,repr(e)

ready=False; mode=None
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        ready=True; mode=body; print('RUNTIME_MODE=',body); break
    time.sleep(2)

for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,8)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'BAR_COUNT=',sum(1 for x in rows if x.get('bar')),'CTX_COUNT=',sum(1 for x in rows if x.get('ctx')),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        for x in rows: print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif not isinstance(body,dict): print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
low=j.stdout.lower()
print('LEGACY_ACTIVITY_COUNTS=',{k:low.count(k) for k in ['minute recovery','tracker warmup','bridge warmup']})
print('WEBSOCKET_FROZEN19_SEEN=',('websocket live: amd/nd,amzn/nd,arm/nd' in low))
print('V205_READY=',ready)
print('NEXT=IF_READY_AND_FROZEN_ROWS_19_AND_CPU_LOW__LEAVE_RUNNING_USA_PAPER_NOW')
