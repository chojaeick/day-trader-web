#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V211 STRUCTURAL DISABLE FROZEN TASK A/B ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=CLEAN_AB_BY_DISABLING_ONLY_FROZEN_EVALUATOR_TASK_USING_CURRENT_RUNTIME_STRUCTURE')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v211')
shutil.copy2(API,bak); print('BACKUP',bak)

# Remove V209 diagnostic pause gate from function body if present, restoring normal function semantics.
src2=src
src2=src2.replace("    global _v209_pause_frozen_loop\n    # V209_PAUSE_GATE: diagnostic only; FE websocket remains alive.\n    while _v209_pause_frozen_loop:\n        await asyncio.sleep(1)\n",'')
src2=src2.replace("_v209_pause_frozen_loop=False\n",'')

# Structurally find startup references to frozen_usa_paper_forever() outside its def.
lines=src2.splitlines(True)
def_line=None
refs=[]
for i,line in enumerate(lines):
    if line.startswith('async def frozen_usa_paper_forever():'):
        def_line=i
    if 'frozen_usa_paper_forever()' in line and not line.lstrip().startswith('async def '):
        refs.append(i)
print('FROZEN_TASK_REF_LINES=',[x+1 for x in refs])
if not refs:
    raise SystemExit('NO_FROZEN_TASK_REFERENCE_FOUND')

# Prefer the lifespan/startup task reference, identified by surrounding tasks.extend/create_task context.
target=None
for i in refs:
    ctx=''.join(lines[max(0,i-8):min(len(lines),i+9)])
    if 'tasks.extend' in ctx or 'tasks.append' in ctx or 'create_task(' in lines[i]:
        target=i
        break
if target is None:
    raise SystemExit('NO_SAFE_STARTUP_FROZEN_TASK_REFERENCE')
print('TARGET_LINE=',target+1,'TEXT=',lines[target].rstrip())

line=lines[target]
# Safely remove only the create_task(frozen...) expression while preserving list syntax.
patterns=[
    r'\s*asyncio\.create_task\(frozen_usa_paper_forever\(\)\),?',
    r'\s*tasks\.append\(asyncio\.create_task\(frozen_usa_paper_forever\(\)\)\)\s*',
]
new_line=line
for pat in patterns:
    new_line2=re.sub(pat,'',new_line,count=1)
    if new_line2!=new_line:
        new_line=new_line2
        break
# If line became empty, keep indentation comment. If closing list parens were on same line and got removed by pattern, abort on compile.
if new_line.strip()=='' or new_line.strip()==',':
    indent=line[:len(line)-len(line.lstrip())]
    new_line=indent+'# V211_AB: frozen evaluator task disabled; FE websocket remains active\n'
else:
    if not new_line.endswith('\n'): new_line+='\n'
    if 'V211_AB' not in new_line:
        new_line=new_line.rstrip('\n')+'  # V211_AB frozen evaluator disabled\n'
lines[target]=new_line
patched=''.join(lines)
API.write_text(patched)

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
    except Exception as e: return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)

time.sleep(8)
p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | grep 'uvicorn live_server.api:app' | grep -v grep | head -1",shell=True,capture_output=True,text=True)
print('UVICORN_CPU_LINE=',p.stdout.strip())

# Frozen endpoint should exist but show no advancing evaluator state; this is expected for A/B.
ok,code,sec,body=get('/api/v4/USA/frozen-paper',5)
print('FROZEN_ENDPOINT OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
if isinstance(body,dict):
    rows=body.get('rows') or []
    print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
else: print('BODY=',body)

j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],capture_output=True,text=True)
text=j.stdout.lower()
print('RECOVERY_BATCH_COUNT=',text.count('minute recovery batch'))
print('LIVE_RECOVERY_COUNT=',text.count('live minute recovery'))
print('BRIDGE_WARM_COUNT=',text.count('bridge warmup'))
print('TRACKER_WARM_COUNT=',text.count('tracker warmup'))
print('NEXT=COMPARE_UVICORN_CPU_WITH_V208_82.7_PERCENT; LOW_CPU=>FROZEN_LOOP_HOTSPOT; HIGH_CPU=>FE_DB_WRITE_HOTSPOT')
