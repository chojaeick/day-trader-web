#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V210 RESTART WITHOUT FROZEN EVALUATOR A/B ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=HARD_AB_TEST_FE_WEBSOCKET_CPU_VS_FROZEN_EVALUATOR_CPU')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v210')
shutil.copy2(API,bak); print('BACKUP',bak)

# Disable only the frozen evaluator task at lifespan startup. Keep FE websocket and V208
# recovery guard unchanged. We deliberately do not use an HTTP pause endpoint.
needle='asyncio.create_task(frozen_usa_paper_forever())'
count=src.count(needle)
print('FROZEN_TASK_CALL_COUNT=',count)
if count<1:
    raise SystemExit('FROZEN_TASK_CALL_NOT_FOUND')
# In the DAYTRADE branch introduced by V206 there should be one active call. Replace all
# active textual calls with a sleeping placeholder for this one-shot A/B; comments untouched.
src2=src.replace(needle,"asyncio.create_task(asyncio.sleep(86400))  # V210_AB_FROZEN_DISABLED")
API.write_text(src2)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API)
    raise SystemExit('COMPILE_FAIL_RESTORED')

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
    except Exception as e:
        return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)

# Sample CPU repeatedly after startup settles.
for wait in (5,10,15):
    time.sleep(wait)
    p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
    print(f'CPU_AFTER_{wait}S=',p.stdout.strip())

# Verify FE ticks continue changing for fixed19 while frozen evaluator is absent.
fixed=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
probe="""
from live_server.config import Settings
from live_server.db import DB
import time
s=Settings(); db=DB(s.db_path)
syms=%r
A={x:(db.ticks(x,1)[0]['ts'] if db.ticks(x,1) else None) for x in syms}
time.sleep(8)
B={x:(db.ticks(x,1)[0]['ts'] if db.ticks(x,1) else None) for x in syms}
chg=[x for x in syms if A.get(x)!=B.get(x)]
print('FE_CHANGED_COUNT=',len(chg),chg)
""" % fixed
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-c',probe],capture_output=True,text=True)
print(r.stdout.strip())
if r.stderr.strip(): print('FE_PROBE_ERR=',r.stderr.strip())

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
low=j.stdout.lower()
print('RECOVERY_BATCH_COUNT=',low.count('minute recovery batch'))
print('LIVE_RECOVERY_COUNT=',low.count('live minute recovery'))
print('FROZEN_LOG_COUNT=',low.count('frozen'))
print('=== JOURNAL_TAIL ==='); print(j.stdout[-7000:])

print('IMPORTANT=THIS_IS_AB_PROBE_ONLY; FROZEN_EVALUATOR_IS_CURRENTLY_DISABLED')
print('NEXT=IF_CPU_DROPS_SHARPLY__REWRITE_FROZEN_LOOP_INCREMENTALLY; IF_CPU_STAYS_HIGH__OPTIMIZE_FE_DB_WRITE_PATH')
