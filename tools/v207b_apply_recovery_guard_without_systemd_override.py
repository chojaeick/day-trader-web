#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, urllib.request, json, shutil

KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
BASE='http://127.0.0.1:8000'
print('=== V207B APPLY RECOVERY GUARD WITHOUT SYSTEMD OVERRIDE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=KEEP_V207_RECOVERY_GUARD; SKIP_SYSTEMD_DROPIN; RESTART_AND_VERIFY')
if not KIO.exists(): raise SystemExit('KIWOOM_NOT_FOUND')
src=KIO.read_text(errors='ignore')
print('GUARD_PRESENT=', 'V207_DAYTRADE_SKIP_MINUTE_RECOVERY' in src)
if 'V207_DAYTRADE_SKIP_MINUTE_RECOVERY' not in src:
    raise SystemExit('V207_GUARD_NOT_PRESENT__DO_NOT_GUESS')

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(KIO)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode: raise SystemExit('COMPILE_FAIL')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
if r.stdout.strip(): print(r.stdout.strip())
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
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        time.sleep(1)

for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
    ok,code,sec,b=get(ep,10)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if isinstance(b,dict) and ep.endswith('frozen-paper'):
        rows=b.get('rows') or []
        print('FROZEN_ROWS=',len(rows),'BAR_COUNT=',sum(1 for x in rows if x.get('bar')),
              'CTX_COUNT=',sum(1 for x in rows if x.get('ctx')),'EVAL=',b.get('evaluations'),
              'ERRORS=',b.get('errors'),'PAPER_EVENTS=',b.get('paper_events'))
        for x in rows:
            print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))
    elif not isinstance(b,dict):
        print('BODY=',b)

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ==='); print(p.stdout)
j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','120','--no-pager'],capture_output=True,text=True)
text=j.stdout.lower()
print('LEGACY_ACTIVITY_COUNTS=',{k:text.count(k) for k in ['minute recovery','tracker warmup','bridge warmup']})
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('USA_PAPER_RUNTIME_READY=',bool(ready and fast))
print('NEXT=IF_FAST_AND_FROZEN_ROWS19_LEAVE_RUNNING_PAPER; ELSE_USE_THIS_OUTPUT_ONLY')
