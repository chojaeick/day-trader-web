#!/usr/bin/env python3
from pathlib import Path
import subprocess, shutil, sys

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BAK=Path('/home/ubuntu/day-trader-api/live_server/api.py.bak_v197')
VENV='/home/ubuntu/day-trader-api/venv/bin/python3'
print('=== V197B RECOVER API AFTER MINUTE-GATE PATCH ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('API_EXISTS=',API.exists(),'BAK_EXISTS=',BAK.exists())

# 1) Show current service state and recent boot failure lines.
for cmd,label in [
    (['systemctl','is-active','day-trader-api.service'],'SERVICE_STATE'),
    (['journalctl','-u','day-trader-api.service','-n','120','--no-pager'],'JOURNAL_LAST120')]:
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        print(label,'RC=',r.returncode)
        print((r.stdout or r.stderr)[-12000:])
    except Exception as e:
        print(label,'ERROR',repr(e))

# 2) Compile and import-probe current api.py under project venv.
if API.exists():
    r=subprocess.run([VENV,'-m','py_compile',str(API)],capture_output=True,text=True)
    print('CURRENT_COMPILE_RC=',r.returncode)
    if r.stdout: print(r.stdout)
    if r.stderr: print(r.stderr)
    try:
        r=subprocess.run([VENV,'-c',"import sys; sys.path.insert(0,'/home/ubuntu/day-trader-api'); import live_server.api; print('IMPORT_OK')"],capture_output=True,text=True,timeout=25)
        print('CURRENT_IMPORT_RC=',r.returncode)
        print((r.stdout or '')[-4000:])
        print((r.stderr or '')[-8000:])
    except Exception as e:
        print('CURRENT_IMPORT_ERROR',repr(e))

# 3) If service is not active and backup exists, restore V197 backup immediately.
state=''
try:
    r=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
    state=(r.stdout or '').strip()
except Exception:
    pass
if state!='active' and BAK.exists():
    shutil.copy2(BAK,API)
    print('ROLLBACK_TO_BAK_V197=YES')
    r=subprocess.run([VENV,'-m','py_compile',str(API)],capture_output=True,text=True)
    print('ROLLBACK_COMPILE_RC=',r.returncode)
    if r.stderr: print(r.stderr)
    rr=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True,timeout=20)
    print('ROLLBACK_RESTART_RC=',rr.returncode)
    if rr.stdout: print(rr.stdout)
    if rr.stderr: print(rr.stderr)
    import time
    for i in range(1,31):
        time.sleep(1)
        r=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
        st=(r.stdout or '').strip()
        if st=='active':
            print('SERVICE_RECOVERED_AFTER_ROLLBACK=YES PROBE',i)
            break
    else:
        print('SERVICE_RECOVERED_AFTER_ROLLBACK=NO')
else:
    print('ROLLBACK_TO_BAK_V197=NO')

print('NEXT=SEND_OUTPUT__THEN_FIX_MINUTE_GATE_AGAINST_EXACT_RUNTIME_SOURCE_ONLY')
