#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V209 SPLIT CPU PROBE FE VS FROZEN LOOP ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=ISOLATE_REMAINING_CPU_TO_FE_WEBSOCKET_OR_FROZEN_TICK_TO_BAR_LOOP')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v209'); shutil.copy2(API,bak); print('BACKUP',bak)

# Locate exact DAYTRADE task split inserted by V206 and make a reversible probe endpoint
# that can pause only the frozen evaluator loop while keeping FE websocket alive.
anchor="asyncio.create_task(frozen_usa_paper_forever())"
print('FROZEN_TASK_ANCHOR_COUNT=',src.count(anchor))
if src.count(anchor)<1: raise SystemExit('FROZEN_TASK_ANCHOR_NOT_FOUND')

# Add pause flag near frozen state if absent.
state_anchor="_frozen_usa_last_bar={}"
if '_v209_pause_frozen_loop' not in src:
    if state_anchor not in src: raise SystemExit('STATE_ANCHOR_NOT_FOUND')
    src=src.replace(state_anchor,state_anchor+"\n_v209_pause_frozen_loop=False",1)

# Add pause gate at top of frozen loop body.
loop_anchor="async def frozen_usa_paper_forever():\n"
pos=src.find(loop_anchor)
print('FROZEN_LOOP_POS=',pos)
if pos<0: raise SystemExit('FROZEN_LOOP_NOT_FOUND')
body_start=pos+len(loop_anchor)
probe=src[body_start:body_start+500]
if 'V209_PAUSE_GATE' not in probe:
    gate=("    global _v209_pause_frozen_loop\n"
          "    # V209_PAUSE_GATE: diagnostic only; FE websocket remains alive.\n"
          "    while _v209_pause_frozen_loop:\n"
          "        await asyncio.sleep(1)\n")
    src=src[:body_start]+gate+src[body_start:]

# Add tiny control endpoint if absent.
if '/api/v4/USA/v209-pause-frozen' not in src:
    ep="""
@app.post('/api/v4/USA/v209-pause-frozen')
def v209_pause_frozen(pause:bool=True):
    global _v209_pause_frozen_loop
    _v209_pause_frozen_loop=bool(pause)
    return {'ok':True,'pause':_v209_pause_frozen_loop}

"""
    insert_at=src.find("@app.get('/api/v4/USA/frozen-paper')")
    if insert_at<0: raise SystemExit('FROZEN_ENDPOINT_ANCHOR_NOT_FOUND')
    src=src[:insert_at]+ep+src[insert_at:]

API.write_text(src)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); raise SystemExit('COMPILE_FAIL_RESTORED')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,timeout=3,method='GET'):
    t=time.time()
    try:
        req=urllib.request.Request(BASE+path,method=method)
        with urllib.request.urlopen(req,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e: return False,0,time.time()-t,repr(e)

def cpu():
    p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
    print('CPU_SNAPSHOT=',p.stdout.strip())
    m=re.search(r'\s(\d+(?:\.\d+)?)\s+\d+(?:\.\d+)?\s+',p.stdout)
    return float(m.group(1)) if m else None

ready=False
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)
print('CPU_BEFORE_PAUSE=',cpu())

# Even if API is sluggish, try pause endpoint with long timeout.
ok,code,sec,body=get('/api/v4/USA/v209-pause-frozen?pause=true',15,'POST')
print('PAUSE_CALL OK=',ok,'HTTP=',code,'SEC=',round(sec,3),'BODY=',body)
if ok:
    time.sleep(15)
    print('CPU_AFTER_FROZEN_PAUSE=',cpu())
    # FE freshness while frozen evaluator paused: DB latest tick ages for fixed19.
    codepy="""
from live_server.config import Settings
from live_server.db import DB
from datetime import datetime,timezone
s=Settings(); d=DB(s.db_path)
syms='AMD AMZN ARM AVGO GOOGL INTC NFLX NVDA ORCL PLTR QQQ SMCI SMH SOXL SOXS SPY SQQQ TQQQ TSM'.split()
for x in syms:
 t=d.ticks(x,1)
 print(x,(t[-1] if t else {}).get('ts') if t else None)
"""
    p=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-c',codepy],cwd='/home/ubuntu/day-trader-api',capture_output=True,text=True,timeout=30)
    print('LATEST_TICKS_WHILE_PAUSED\n'+p.stdout)
    ok2,code2,sec2,body2=get('/api/v4/USA/v209-pause-frozen?pause=false',15,'POST')
    print('RESUME_CALL OK=',ok2,'HTTP=',code2,'SEC=',round(sec2,3),'BODY=',body2)
else:
    print('PAUSE_ENDPOINT_UNRESPONSIVE')

# Thread/process snapshot for exact hotspot clue.
p=subprocess.run("ps -L -p $(pgrep -f 'uvicorn live_server.api:app' | head -1) -o pid,tid,pcpu,stat,comm --sort=-pcpu | head -20",shell=True,capture_output=True,text=True)
print('=== THREAD TOP ===\n'+p.stdout)
print('NEXT=IF_CPU_DROPS_SHARPLY_WHEN_FROZEN_PAUSED__OPTIMIZE_FROZEN_LOOP; IF_CPU_STAYS_HIGH__FE_DB_WRITE_PATH_IS_HOTSPOT')
