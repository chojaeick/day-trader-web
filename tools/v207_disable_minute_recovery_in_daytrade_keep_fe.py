#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
BASE='http://127.0.0.1:8000'
print('=== V207 DISABLE MINUTE RECOVERY IN DAYTRADE KEEP FE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=KEEP_FE_WEBSOCKET_REALTIME_BUT_SKIP_CPU_HEAVY_LIVE_MINUTE_RECOVERY_WHILE_DAYTRADE')
if not KIO.exists(): raise SystemExit('KIO_NOT_FOUND')
src=KIO.read_text(errors='ignore')
bak=Path(str(KIO)+'.bak_v207'); shutil.copy2(KIO,bak); print('BACKUP',bak)

# Discover exact runtime log/call context instead of guessing line numbers.
needles=['live minute recovery','minute recovery batch priority']
for n in needles:
    p=src.find(n)
    print('FIND',n,'POS=',p)
    if p>=0: print('CONTEXT',repr(src[max(0,p-1200):p+1800]))

# Patch helper/call sites by guarding recovery work with runtime mode env flag exposed by api.
# api.py and kiwoom.py are same process; use environment toggle set by V206 task split.
# We intentionally do not alter websocket receive/FE parsing.
# First locate likely async recovery helper definition from log string.
p=src.find('live minute recovery')
if p<0: raise SystemExit('LIVE_MINUTE_RECOVERY_LOG_NOT_FOUND')
start=src.rfind('\n    async def ',0,p)
if start<0: start=src.rfind('\n    def ',0,p)
if start<0: raise SystemExit('RECOVERY_DEF_NOT_FOUND')
line_end=src.find('\n',start+1)
header=src[start+1:line_end]
print('RECOVERY_HEADER=',header)
indent='        '
guard="\n        if __import__('os').environ.get('DAYTRADER_DISABLE_LIVE_MINUTE_RECOVERY')=='1':\n            return 0\n"
if 'DAYTRADER_DISABLE_LIVE_MINUTE_RECOVERY' not in src[start:line_end+500]:
    src=src[:line_end]+guard+src[line_end:]
    print('RECOVERY_GUARD_INSERTED=True')
else:
    print('RECOVERY_GUARD_ALREADY=True')

# Ensure API service sets the env flag for DAYTRADE isolation via systemd Environment override file.
KIO.write_text(src)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(KIO)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,KIO)
    raise SystemExit('COMPILE_FAIL_RESTORED')

override=Path('/etc/systemd/system/day-trader-api.service.d/v207-daytrade-recovery.conf')
override.parent.mkdir(parents=True,exist_ok=True)
override.write_text('[Service]\nEnvironment=DAYTRADER_DISABLE_LIVE_MINUTE_RECOVERY=1\n')
print('SYSTEMD_OVERRIDE=',override)
subprocess.run(['sudo','systemctl','daemon-reload'])
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

lat=[]
if ready:
    for i in range(5):
        ok,code,sec,body=get('/api/v4/runtime-mode',3)
        lat.append(sec if ok else 99)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3)); time.sleep(1)

for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,10)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'BAR_COUNT=',sum(1 for x in rows if x.get('bar')),'CTX_COUNT=',sum(1 for x in rows if x.get('ctx')),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        for x in rows: print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif not isinstance(body,dict): print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','120','--no-pager'],capture_output=True,text=True)
text=j.stdout.lower(); print('MINUTE_RECOVERY_COUNT=',text.count('live minute recovery'),'BATCH_COUNT=',text.count('minute recovery batch priority'))
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast))
print('NEXT=IF_READY_AND_FROZEN_ROWS19_LEAVE_RUNNING; ELSE_INSPECT_ONLY_REMAINING_FROZEN_LOOP_COST')
