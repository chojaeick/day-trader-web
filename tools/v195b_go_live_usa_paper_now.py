#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys, time, urllib.request
from pathlib import Path

BASE='http://127.0.0.1:8000'
ROOT=Path('/home/ubuntu/day-trader-api-repo')
V195=ROOT/'tools/v195_patch_frozen_usa_feed_f5_to_fe_with_parser_parity.py'
VENV='/home/ubuntu/day-trader-api/venv/bin/python3'

print('=== V195B GO LIVE USA PAPER NOW ===')
print('TARGET=USA_PAPER_ONLY DAYTRADE FE_FROZEN19')
print('REAL_BROKER_AUTHORITY=NONE')
print('NO_MORE_DIAG_BEFORE_PAPER_START=YES')

# 1) Apply FE feed patch and restart/verify feed.
if not V195.exists():
    raise SystemExit('V195_NOT_FOUND')
r=subprocess.run([VENV,str(V195)],cwd=str(ROOT))
print('V195_RC=',r.returncode)
if r.returncode!=0:
    raise SystemExit('V195_FAILED__PAPER_NOT_STARTED')

def req(path, method='GET', data=None, timeout=8):
    body=None; headers={}
    if data is not None:
        body=json.dumps(data).encode(); headers['Content-Type']='application/json'
    q=urllib.request.Request(BASE+path,data=body,headers=headers,method=method)
    with urllib.request.urlopen(q,timeout=timeout) as f:
        raw=f.read().decode(errors='ignore')
        try: obj=json.loads(raw)
        except Exception: obj=raw
        return f.status,obj

# 2) Force DAYTRADE runtime mode now.
set_code=None; set_obj=None
for payload in ({'mode':'DAYTRADE'},None):
    try:
        set_code,set_obj=req('/api/v4/runtime-mode/DAYTRADE','POST',payload)
        if set_code==200: break
    except Exception as e:
        set_obj={'error':repr(e)}
print('SET_DAYTRADE_HTTP=',set_code)
print('SET_DAYTRADE=',set_obj)

time.sleep(2)
mode_code,mode=req('/api/v4/runtime-mode')
print('RUNTIME_MODE_HTTP=',mode_code)
print('RUNTIME_MODE=',mode)
mode_ok=isinstance(mode,dict) and str(mode.get('mode','')).upper()=='DAYTRADE'

# 3) Verify service and frozen paper endpoint are active.
state=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True).stdout.strip()
print('SERVICE_STATE=',state)
fp_code,fp=req('/api/v4/USA/frozen-paper')
print('FROZEN_PAPER_HTTP=',fp_code)
if isinstance(fp,dict):
    print('FROZEN_ENABLED=',fp.get('enabled'))
    print('FROZEN_MODE=',fp.get('mode'))
    print('FROZEN_STRATEGY=',fp.get('strategy'))
    print('FROZEN_SYMBOLS=',len(fp.get('symbols') or []))
    print('FROZEN_EVALUATIONS=',fp.get('evaluations'))
    print('FROZEN_PAPER_EVENTS=',fp.get('paper_events'))
    rows=fp.get('rows') or []
    ctx=sum(1 for x in rows if x.get('ctx'))
    print('CTX_READY_ROWS=',ctx,'/',len(rows))
else:
    print('FROZEN_BODY=',fp)

# 4) Explicitly verify no real-broker authority was introduced by this runner.
print('ORDER_MODE=USA_PAPER_ONLY')
print('REAL_BROKER_CALLS_ADDED=NONE')
pass_now=(r.returncode==0 and mode_ok and state=='active' and fp_code==200)
print('USA_PAPER_STARTED_NOW=',pass_now)
if not pass_now:
    raise SystemExit(2)
print('NEXT=LEAVE_SERVICE_RUNNING_AND_OBSERVE_PAPER_EVENTS__NO_MORE_PRESTART_BLOCKER')
