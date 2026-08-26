#!/usr/bin/env python3
"""V149 verify runtime mode and live USA frozen-context feed.

Read-only audit. Service must already be running. No broker calls/orders.
Checks runtime mode endpoint/state, USA status health, presence of frozen-context
telemetry in live rows, and reports whether paper validation can proceed.
"""
from __future__ import annotations
import json, subprocess, urllib.request, urllib.error, time

BASE='http://127.0.0.1:8000'

def get(path):
    try:
        with urllib.request.urlopen(BASE+path, timeout=8) as r:
            return r.status, json.loads(r.read().decode('utf-8','ignore'))
    except Exception as e:
        return None, {'error':str(e)}

print('=== V149 VERIFY RUNTIME MODE + USA LIVE FEED ===')
print('READ_ONLY=YES ORDERS=NONE SERVICE_MUTATION=NONE')

try:
    p=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
    state=(p.stdout or p.stderr).strip()
except Exception as e:
    state='UNKNOWN:'+str(e)
print('SERVICE_STATE=',state)

# Try known runtime-mode endpoints without mutating anything.
mode=None;mode_src=None
for path in ['/api/v4/runtime-mode','/api/v4/runtime_mode','/api/v4/mode']:
    st,obj=get(path)
    if st==200:
        mode=obj;mode_src=path;break
print('RUNTIME_MODE_SOURCE=',mode_src or 'NOT_FOUND')
print('RUNTIME_MODE=',json.dumps(mode,ensure_ascii=False) if mode is not None else 'UNKNOWN')

st,status=get('/api/v4/USA/status')
print('USA_STATUS_HTTP=',st)
print('USA_STATUS_OK=',bool(st==200 and isinstance(status,dict)))
if isinstance(status,dict):
    for k in ['session','market','mode','runtime_mode','updated_at','state']:
        if k in status: print('USA_STATUS',k,'=',status.get(k))

# Discover likely live row endpoints, inspect JSON recursively for frozen telemetry.
def walk(x,path=''):
    if isinstance(x,dict):
        for k,v in x.items():
            p=f'{path}.{k}' if path else k
            yield p,v
            yield from walk(v,p)
    elif isinstance(x,list):
        for i,v in enumerate(x[:100]):
            yield from walk(v,f'{path}[{i}]')

endpoints=['/api/v4/USA/tracker','/api/v4/USA/ranking','/api/v4/USA/state','/api/v4/USA/status']
frozen_hits=[];ctx_hits=[];rows_seen=0;live_ok=False
for ep in endpoints:
    s,o=get(ep)
    if s!=200: continue
    live_ok=True
    if isinstance(o,list): rows_seen+=len(o)
    elif isinstance(o,dict):
        for key in ['rows','items','data','tracker','ranking']:
            if isinstance(o.get(key),list): rows_seen+=len(o[key])
    for p,v in walk(o):
        pl=p.lower()
        if 'williams_frozen_eval' in pl:
            frozen_hits.append((ep,p,v))
        if 'williams_frozen_ctx' in pl:
            ctx_hits.append((ep,p,v))

print('LIVE_ENDPOINT_OK=',live_ok)
print('ROWS_SEEN=',rows_seen)
print('FROZEN_EVAL_HITS=',len(frozen_hits))
print('FROZEN_CTX_HITS=',len(ctx_hits))
for tag,hits in [('CTX',ctx_hits),('EVAL',frozen_hits)]:
    for ep,p,v in hits[:5]:
        s=str(v)
        print(tag,ep,p,'=',s[:300])

# mode safety: DAYTRADE preferred; if endpoint unavailable, do not guess.
mode_text=json.dumps(mode,ensure_ascii=False).upper() if mode is not None else ''
mode_daytrade=('DAYTRADE' in mode_text)
print('MODE_DAYTRADE=',mode_daytrade)
print('USA_FROZEN_CONTEXT_VISIBLE=',bool(ctx_hits or frozen_hits))

ready=bool(state=='active' and st==200 and live_ok and mode_daytrade and (ctx_hits or frozen_hits))
print('V149_PASS=',ready)
if not mode_daytrade:
    print('BLOCKER=RUNTIME_MODE_NOT_CONFIRMED_DAYTRADE')
elif not (ctx_hits or frozen_hits):
    print('BLOCKER=LIVE_FROZEN_CONTEXT_NOT_VISIBLE_YET')
print('NEXT=' + ('V150_OBSERVE_FORWARD_USA_PAPER_SIGNALS' if ready else 'FIX_ONLY_RUNTIME_MODE_OR_LIVE_FEED_VISIBILITY; NO_STRATEGY_CHANGE'))
