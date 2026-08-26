#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V208 PATCH RECOVERY GUARD WITH RUNTIME FLAG + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=SKIP_CPU_HEAVY_MINUTE_RECOVERY_IN_DAYTRADE_WITHOUT_SYSTEMD_OVERRIDE')
if not KIO.exists() or not API.exists(): raise SystemExit('RUNTIME_FILE_MISSING')
for p in (KIO,API): shutil.copy2(p,Path(str(p)+'.bak_v208'))

ks=KIO.read_text(errors='ignore')
# Exact observed block from V207 output. Insert a runtime attribute guard before recovery_batch work.
anchor="                recovery_batch=priority+rotating\n\n                log.info(\n                    'V4 minute recovery batch priority=%s rotating=%s',"
print('RECOVERY_ANCHOR_COUNT=',ks.count(anchor))
if ks.count(anchor)!=1:
    raise SystemExit('RECOVERY_ANCHOR_NOT_EXACTLY_ONE')
replacement=("                recovery_batch=priority+rotating\n\n"
             "                if bool(getattr(self,'disable_minute_recovery_daytrade',False)):\n"
             "                    last_regular_run=now_ts\n"
             "                    await asyncio.sleep(5)\n"
             "                    continue\n\n"
             "                log.info(\n"
             "                    'V4 minute recovery batch priority=%s rotating=%s',")
ks=ks.replace(anchor,replacement,1)
KIO.write_text(ks)

aps=API.read_text(errors='ignore')
# Set flag in runtime initialization, no systemd env needed.
anchor2="v4=CleanEngine(s.db_path)\n"
print('API_FLAG_ANCHOR_COUNT=',aps.count(anchor2))
if aps.count(anchor2)!=1:
    raise SystemExit('API_FLAG_ANCHOR_NOT_EXACTLY_ONE')
flag="v4=CleanEngine(s.db_path)\nk.disable_minute_recovery_daytrade=True  # V208 frozen19 DAYTRADE load shed; FE websocket remains enabled\n"
aps=aps.replace(anchor2,flag,1)
API.write_text(aps)

for p in (KIO,API):
    r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(p)],capture_output=True,text=True)
    print('COMPILE',p.name,'RC=',r.returncode)
    if r.stderr.strip(): print(r.stderr.strip())
    if r.returncode:
        shutil.copy2(Path(str(KIO)+'.bak_v208'),KIO); shutil.copy2(Path(str(API)+'.bak_v208'),API)
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
for i in range(1,41):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)

lat=[]
if ready:
    for i in range(6):
        ok,code,sec,body=get('/api/v4/runtime-mode',3)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        time.sleep(1)

frozen_ok=False; rows=[]
for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,body=get(ep,10)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(body,dict) and ep.endswith('frozen-paper'):
        rows=body.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'BAR_COUNT=',sum(1 for x in rows if x.get('bar')),'CTX_COUNT=',sum(1 for x in rows if x.get('ctx')),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
        for x in rows: print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
        frozen_ok=(ok and code==200 and len(rows)==19)
    elif not isinstance(body,dict): print('BODY=',body)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
# Journal only from current boot window-ish tail; recovery should be absent after restart.
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
text=j.stdout
print('CURRENT_TAIL_RECOVERY_BATCH_COUNT=',text.count('V4 minute recovery batch'))
print('CURRENT_TAIL_LIVE_RECOVERY_COUNT=',text.count('live minute recovery'))
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V208_PASS=',bool(ready and fast and frozen_ok))
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast and frozen_ok and any(x.get('bar') for x in rows)))
print('NEXT=IF_READY_LEAVE_RUNNING_PAPER; ELSE_SEND_OUTPUT_FOR_ONE_FINAL_CONFIRMED_FIX')
