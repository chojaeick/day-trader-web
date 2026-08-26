#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V206 PATCH EXACT OBSERVED LIFESPAN BLOCK ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=USE_V205_OBSERVED_EXACT_TASK_BLOCK_TO_ISOLATE_DAYTRADE_FROZEN19')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v206'); shutil.copy2(API,bak); print('BACKUP',bak)

old="""        tasks.extend([asyncio.create_task(k.websocket_forever()),
                       # V180 disabled: second USA websocket session is rejected/closed by Kiwoom (1000 OK Bye).
                       # asyncio.create_task(k.frozen19_websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
                      asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
                      asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
                      asyncio.create_task(williams_mock_hard_stop_forever()),
                      asyncio.create_task(v4_engine_forever()),
                       asyncio.create_task(frozen_usa_paper_forever())])
    tasks.append(asyncio.create_task(fujimoto_auto_forever()))
    tasks.append(asyncio.create_task(fujimoto_auto_v4_forever()))
    tasks.append(asyncio.create_task(daytrade_entry_auto_forever()))
"""
new="""        if _runtime_profile().get('mode')=='DAYTRADE':
            # V206: frozen19 paper authority only. Keep one Kiwoom WS session + frozen evaluator.
            tasks.extend([
                asyncio.create_task(k.websocket_forever()),
                asyncio.create_task(frozen_usa_paper_forever()),
            ])
        else:
            # NORMAL mode: preserve legacy startup behavior unchanged.
            tasks.extend([asyncio.create_task(k.websocket_forever()),
                           # V180 disabled: second USA websocket session is rejected/closed by Kiwoom (1000 OK Bye).
                           # asyncio.create_task(k.frozen19_websocket_forever()),asyncio.create_task(k.snapshot_poll_forever()),
                          asyncio.create_task(k.daily_refresh_forever()),asyncio.create_task(k.backfill_forever_once()),
                          asyncio.create_task(k.discovery_forever()),asyncio.create_task(checkpoint_forever()),
                          asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                          asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
                          asyncio.create_task(williams_mock_hard_stop_forever()),
                          asyncio.create_task(v4_engine_forever()),
                           asyncio.create_task(frozen_usa_paper_forever())])
            tasks.append(asyncio.create_task(fujimoto_auto_forever()))
            tasks.append(asyncio.create_task(fujimoto_auto_v4_forever()))
            tasks.append(asyncio.create_task(daytrade_entry_auto_forever()))
"""
count=src.count(old)
print('EXACT_OBSERVED_BLOCK_COUNT=',count)
if count!=1:
    p=src.find('tasks.extend([asyncio.create_task(k.websocket_forever())')
    print('CONTEXT=',repr(src[max(0,p-400):p+1800] if p>=0 else 'NOT_FOUND'))
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

ready=False; body=None
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)

lat=[]
if ready:
    for i in range(6):
        ok,code,sec,b=get('/api/v4/runtime-mode',3)
        lat.append(sec if ok else 99)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        time.sleep(1)

frozen_rows=0; frozen_bars=0; frozen_ctx=0; frozen_ok=False
for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,b=get(ep,10)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(b,dict) and ep.endswith('frozen-paper'):
        rows=b.get('rows') or []; frozen_rows=len(rows)
        frozen_bars=sum(1 for x in rows if x.get('bar')); frozen_ctx=sum(1 for x in rows if x.get('ctx'))
        frozen_ok=ok and code==200 and frozen_rows==19
        print('FROZEN_ROWS=',frozen_rows,'BAR_COUNT=',frozen_bars,'CTX_COUNT=',frozen_ctx,'EVAL=',b.get('evaluations'),'ERRORS=',b.get('errors'),'PAPER_EVENTS=',b.get('paper_events'))
        for x in rows: print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif isinstance(b,dict): print('STATUS_MODE=',b.get('mode'),'SESSION=',b.get('session'))
    else: print('BODY=',b)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
text=j.stdout.lower()
print('LEGACY_ACTIVITY_COUNTS=',{k:text.count(k) for k in ['minute recovery','tracker warmup','bridge warmup']})
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V206_PASS=',bool(ready and fast and frozen_ok))
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast and frozen_ok and frozen_bars>0))
print('NEXT=IF_PASS_LEAVE_RUNNING_PAPER__IF_FAIL_ONLY_INSPECT_CURRENT_MODE_AND_STARTUP_TASK_SELECTION')
