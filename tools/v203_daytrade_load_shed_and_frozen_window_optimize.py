#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V203 DAYTRADE LOAD SHED + FROZEN WINDOW OPTIMIZE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=PRIORITIZE_FROZEN19_PAPER_RUNTIME_AND_REMOVE_CPU_STARVATION')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v203')
shutil.copy2(API,bak); print('BACKUP',bak)

# 1) Frozen loop still rebuilds 12k ticks x 19 every new minute. 5k preserves >=25m
# for observed FE rates while cutting the minute burst by >58%.
old='ticks=await asyncio.to_thread(db.ticks,sym,12000)'
new='ticks=await asyncio.to_thread(db.ticks,sym,5000)  # V203 FE window: enough for >=25 completed 1m bars at observed rates'
c1=src.count(old)
print('FROZEN_12000_TARGET_COUNT=',c1)
if c1==1:
    src=src.replace(old,new,1)
else:
    print('FROZEN_TARGET_CONTEXT')
    p=src.find('db.ticks,sym,12000')
    print(repr(src[max(0,p-180):p+260] if p>=0 else 'NOT_FOUND'))

# 2) During DAYTRADE, frozen19 is the trading authority. The legacy V4 engine loop
# continuously runs finder/warm/recovery work and is not needed to execute frozen paper
# entries/exits. Pause only this legacy loop while DAYTRADE is active; NORMAL stays unchanged.
marker="async def v4_engine_forever():\n"
pos=src.find(marker)
print('V4_ENGINE_DEF_POS=',pos)
inserted=False
if pos>=0:
    body_start=pos+len(marker)
    probe=src[body_start:body_start+500]
    if 'V203_DAYTRADE_LOAD_SHED' in probe:
        print('V4_ENGINE_DAYTRADE_SHED_ALREADY_PRESENT=True')
        inserted=True
    else:
        # Find initial indentation/body and place guard before legacy work.
        guard=("    # V203_DAYTRADE_LOAD_SHED: frozen19 paper is the DAYTRADE authority.\n"
               "    # Keep NORMAL legacy engine behavior unchanged.\n"
               "    while _runtime_profile().get('mode')=='DAYTRADE':\n"
               "        await asyncio.sleep(30)\n")
        src=src[:body_start]+guard+src[body_start:]
        inserted=True
        print('V4_ENGINE_DAYTRADE_SHED_INSERTED=True')
else:
    print('V4_ENGINE_DEF_NOT_FOUND')

if c1!=1 or not inserted:
    raise SystemExit('PATCH_TARGET_MISMATCH')

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

def get(path,timeout=4):
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
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',4)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body)
        ready=True
        break
    time.sleep(3)
print('API_READY=',ready)

lat=[]
if ready:
    for i in range(8):
        ok,code,sec,body=get('/api/v4/runtime-mode',4)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        time.sleep(1)

frozen_ok=False; frozen_rows=0; frozen_bars=0; frozen_ctx=0
for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,12)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        frozen_rows=len(rows)
        frozen_bars=sum(1 for x in rows if x.get('bar'))
        frozen_ctx=sum(1 for x in rows if x.get('ctx'))
        frozen_ok=(ok and code==200 and frozen_rows==19)
        print('FROZEN_ROWS=',frozen_rows,'BAR_COUNT=',frozen_bars,'CTX_COUNT=',frozen_ctx,
              'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        for x in rows:
            print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif isinstance(body,dict):
        print('STATUS_SESSION=',body.get('session'),'MODE=',body.get('mode'))
    else:
        print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -12",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ===')
print(p.stdout)
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V203_PASS=',bool(ready and fast and frozen_ok))
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast and frozen_ok and frozen_bars>0))
print('NEXT=IF_PASS_LEAVE_RUNNING_AND_OBSERVE_FROZEN_PAPER_EVENTS__NO_MORE_PRESTART_DIAG')
if not ready or not fast:
    j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
    print('=== JOURNAL_LAST80 ===')
    print(j.stdout[-12000:])
