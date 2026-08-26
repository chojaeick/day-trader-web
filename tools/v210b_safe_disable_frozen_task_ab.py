#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V210B SAFE DISABLE FROZEN EVALUATOR TASK A/B ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=SAFELY_REMOVE_ONLY_FROZEN_EVALUATOR_TASK_FROM_DAYTRADE_STARTUP_FOR_CPU_AB')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v210b')
shutil.copy2(API,bak); print('BACKUP',bak)

# Exact observed DAYTRADE branch from V206 patch. Replace only the one frozen task line,
# preserving websocket and list syntax.
old="""        tasks.extend([asyncio.create_task(k.websocket_forever()),
                      asyncio.create_task(frozen_usa_paper_forever())])
"""
new="""        tasks.extend([asyncio.create_task(k.websocket_forever())])  # V210B A/B: frozen evaluator disabled only
"""
count=src.count(old)
print('EXACT_DAYTRADE_FROZEN_BLOCK_COUNT=',count)
if count!=1:
    # show exact local context, no mutation
    p=src.find('frozen_usa_paper_forever()')
    print('CONTEXT=',repr(src[max(0,p-500):p+500] if p>=0 else 'NOT_FOUND'))
    raise SystemExit('EXACT_BLOCK_NOT_FOUND__NO_MUTATION')
src=src.replace(old,new,1)
API.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API)
    print('COMPILE_FAIL_RESTORED')
    raise SystemExit(1)

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())

def get(path,timeout=3):
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
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        ready=True; print('RUNTIME_MODE=',body); break
    time.sleep(2)

# Let CPU settle a little after startup.
time.sleep(10)
p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)

# Frozen endpoint should still exist but state should not advance while evaluator is disabled.
ok,code,sec,body=get('/api/v4/USA/frozen-paper',8)
print('FROZEN_ENDPOINT OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
if isinstance(body,dict):
    rows=body.get('rows') or []
    print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
else:
    print('BODY=',body)

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
text=j.stdout
print('RECOVERY_BATCH_COUNT=',text.lower().count('minute recovery batch'))
print('LIVE_RECOVERY_COUNT=',text.lower().count('live minute recovery'))
print('=== JOURNAL_TAIL ==='); print(text[-8000:])
print('AB_STATE=FROZEN_EVALUATOR_DISABLED__DO_NOT_LEAVE_FOR_PAPER')
print('NEXT=COMPARE_UVICORN_CPU_TO_V208; IF_LOW_FROZEN_LOOP_HOTSPOT; IF_HIGH_FE_DB_WRITE_HOTSPOT')
