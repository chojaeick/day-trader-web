#!/usr/bin/env python3
"""V150: set runtime mode to DAYTRADE, then recheck USA frozen live feed visibility.

Only mutates runtime mode through the local API. No strategy changes. No broker calls.
"""
from __future__ import annotations
import json, subprocess, time, urllib.request

BASE='http://127.0.0.1:8000'

def http_json(path, method='GET', data=None, timeout=8):
    body=None
    headers={}
    if data is not None:
        body=json.dumps(data).encode()
        headers['Content-Type']='application/json'
    req=urllib.request.Request(BASE+path,data=body,headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read().decode(errors='ignore')
        try:return r.status,json.loads(raw)
        except Exception:return r.status,raw

print('=== V150 SET DAYTRADE + RECHECK USA FROZEN FEED ===')
print('STRATEGY_CHANGE=NONE BROKER_CALLS=NONE')

try:
    rc=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
    state=(rc.stdout or rc.stderr).strip()
except Exception as e:
    state='UNKNOWN:'+str(e)
print('SERVICE_STATE=',state)
if state!='active':
    print('V150_PASS=False')
    print('BLOCKER=SERVICE_NOT_ACTIVE')
    raise SystemExit(2)

# Set DAYTRADE mode.
set_status=set_obj=None
for payload in ({'mode':'DAYTRADE'}, None):
    try:
        if payload is not None:
            set_status,set_obj=http_json('/api/v4/runtime-mode/DAYTRADE',method='POST',data=payload)
        else:
            set_status,set_obj=http_json('/api/v4/runtime-mode/DAYTRADE',method='POST')
        if set_status==200: break
    except Exception as e:
        set_obj={'error':str(e)}
print('SET_MODE_HTTP=',set_status)
print('SET_MODE_RESPONSE=',json.dumps(set_obj,ensure_ascii=False) if isinstance(set_obj,(dict,list)) else set_obj)

time.sleep(2)
try:
    st,obj=http_json('/api/v4/runtime-mode')
except Exception as e:
    st,obj=0,{'error':str(e)}
print('RUNTIME_MODE_HTTP=',st)
print('RUNTIME_MODE=',json.dumps(obj,ensure_ascii=False) if isinstance(obj,(dict,list)) else obj)
mode=str(obj.get('mode','')).upper() if isinstance(obj,dict) else ''
mode_ok=(mode=='DAYTRADE')
print('MODE_DAYTRADE=',mode_ok)

# Probe USA endpoints; collect any rows recursively from returned JSON.
endpoints=['/api/v4/USA/status','/api/v4/USA/tracker','/api/v4/USA/finder']
rows=[]

def walk(x):
    if isinstance(x,dict):
        if str(x.get('market','')).upper()=='USA' or 'symbol' in x:
            rows.append(x)
        for v in x.values(): walk(v)
    elif isinstance(x,list):
        for v in x: walk(v)

for ep in endpoints:
    try:
        code,data=http_json(ep)
        print('ENDPOINT',ep,'HTTP=',code)
        if isinstance(data,dict):
            for k in ('session','market','mode'):
                if k in data: print(' ',k,'=',data.get(k))
        walk(data)
    except Exception as e:
        print('ENDPOINT',ep,'ERROR=',e)

# Deduplicate row object identities by serialized content.
uniq=[];seen=set()
for r in rows:
    try:key=json.dumps(r,sort_keys=True,default=str)
    except Exception:key=str(r)
    if key not in seen:
        seen.add(key);uniq.append(r)
ctx_hits=sum(1 for r in uniq if r.get('williams_frozen_ctx'))
eval_hits=sum(1 for r in uniq if r.get('williams_frozen_eval'))
print('ROWS_SEEN=',len(uniq))
print('FROZEN_CTX_HITS=',ctx_hits)
print('FROZEN_EVAL_HITS=',eval_hits)
visible=(ctx_hits>0 or eval_hits>0)
print('USA_FROZEN_CONTEXT_VISIBLE=',visible)

# Premarket may legitimately have no actionable rows; distinguish visibility from mode blocker.
pass_now=bool(mode_ok)
print('V150_PASS=',pass_now)
if not mode_ok:
    print('BLOCKER=RUNTIME_MODE_SET_FAILED')
elif visible:
    print('NEXT=V151_LIVE_FROZEN_TELEMETRY_OBSERVATION')
else:
    print('NEXT=V151_EXPOSE_OR_VERIFY_USA_ROW_TELEMETRY_WITHOUT_STRATEGY_CHANGE')
