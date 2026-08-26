#!/usr/bin/env python3
from pathlib import Path
import re, shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V201 PATCH REMAINING V4 ASYNC BLOCKERS + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=MOVE_SYNC_USA_FINDER_AND_BRIDGE_WARMUP_OFF_EVENT_LOOP')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v201')
shutil.copy2(API,bak); print('BACKUP',bak)

# 1) warm_bridge_candidates is network/heavy work and must never run inline in async v4_engine_forever.
old='warm_bridge_candidates(usa_candidates,k.discovery)'
new='await asyncio.to_thread(warm_bridge_candidates,usa_candidates,k.discovery)'
c1=src.count(old)
print('WARM_BRIDGE_TARGET_COUNT=',c1)
if c1==1:
    src=src.replace(old,new,1)
elif new in src:
    print('WARM_BRIDGE_ALREADY_PATCHED=YES')
else:
    raise SystemExit('WARM_BRIDGE_EXACT_TARGET_NOT_FOUND')

# 2) build_usa_finder can also perform DB/CPU work. Match only the exact v4_engine_forever call shape.
pat=re.compile(r'usa_candidates\s*=\s*v4\.build_usa_finder\(\s*k\.discovery\s*,\s*5\s*,\s*db\s*=\s*db\s*\)',re.S)
m=list(pat.finditer(src))
print('USA_FINDER_TARGET_COUNT=',len(m))
if len(m)==1:
    src=pat.sub('usa_candidates=await asyncio.to_thread(v4.build_usa_finder,k.discovery,5,db=db)',src,count=1)
elif 'usa_candidates=await asyncio.to_thread(v4.build_usa_finder,k.discovery,5,db=db)' in src:
    print('USA_FINDER_ALREADY_PATCHED=YES')
else:
    # Do not fail the confirmed bridge fix merely because formatting differs; print exact nearby context.
    pos=src.find('k.discovery,5,db=db')
    print('USA_FINDER_EXACT_REGEX_NOT_FOUND POS=',pos)
    if pos>=0: print('USA_FINDER_CONTEXT=',repr(src[max(0,pos-180):pos+180]))

API.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode: raise SystemExit('COMPILE_FAIL')

# Restart. Existing starved process may take up to systemd StopTimeoutSec before kill.
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
    ok,code,sec,body=get('/api/v4/runtime-mode',timeout=4)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        ready=True; print('RUNTIME_MODE=',body); break
    time.sleep(3)
print('API_READY=',ready)
if not ready:
    p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
    print(p.stdout)
    j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
    print(j.stdout[-14000:])
    raise SystemExit('API_STILL_NOT_RESPONSIVE')

# Latency should stay responsive repeatedly, not merely one lucky request.
lat=[]
for i in range(8):
    ok,code,sec,body=get('/api/v4/runtime-mode',timeout=4)
    lat.append(sec if ok else 99.0)
    print('LAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    time.sleep(1)

for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,timeout=12)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'),'UPDATED=',body.get('updated_at'))
        print('FROZEN_BAR_COUNT=',sum(1 for x in rows if x.get('bar')),'FROZEN_CTX_COUNT=',sum(1 for x in rows if x.get('ctx')))
        for x in rows:
            print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif isinstance(body,dict):
        print('STATUS_SESSION=',body.get('session'),'MODE=',body.get('mode'))
    else:
        print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ===')
print(p.stdout)
fast=max(lat)<2.0 if lat else False
print('RUNTIME_MODE_FAST=',fast)
print('V201_PASS=',bool(ready and fast))
print('NEXT=IF_FROZEN_ROWS_19_AND_CURRENT_BAR_CTX_PRESENT__USA_PAPER_RUNTIME_DONE; ELSE_FIX_ONLY_FROZEN_LOOP_OUTPUT')