#!/usr/bin/env python3
from __future__ import annotations
import json,time,urllib.request
BASE='http://127.0.0.1:8000'

def get(path):
    try:
        with urllib.request.urlopen(BASE+path,timeout=8) as r:
            return r.status,json.loads(r.read().decode('utf-8','ignore'))
    except Exception as e:
        return None,{'error':str(e)}

def rows(obj):
    out=[]
    if isinstance(obj,dict):
        if isinstance(obj.get('rows'),list): out += [x for x in obj['rows'] if isinstance(x,dict)]
        for v in obj.values():
            if isinstance(v,(dict,list)): out += rows(v)
    elif isinstance(obj,list):
        for v in obj:
            if isinstance(v,(dict,list)): out += rows(v)
    return out

print('=== V152 USA RUNTIME OBSERVER ===')
mode_ok=False; eval_hits=ctx_hits=errors=0; sessions=[]
for n in range(4):
    print('PASS',n+1)
    st,m=get('/api/v4/runtime-mode'); print('MODE',st,m)
    if isinstance(m,dict) and m.get('mode')=='DAYTRADE': mode_ok=True
    for p in ('/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker'):
        st,o=get(p); print(p,st,o.get('error') if isinstance(o,dict) else None)
        if isinstance(o,dict) and o.get('session'): sessions.append(str(o.get('session')).upper())
        for r in rows(o):
            if isinstance(r.get('williams_frozen_eval'),dict):
                eval_hits+=1
                if r['williams_frozen_eval'].get('error'): errors+=1
            if isinstance(r.get('williams_frozen_ctx'),dict): ctx_hits+=1
    if n<3: time.sleep(5)
print('MODE_DAYTRADE=',mode_ok)
print('SESSIONS=',sessions)
print('FROZEN_EVAL_HITS=',eval_hits)
print('FROZEN_CTX_HITS=',ctx_hits)
print('FROZEN_ERRORS=',errors)
print('V152_READY_NOW=',bool(mode_ok and eval_hits>0 and errors==0))
print('NOTE=PREMARKET observation is active; frozen entry rule remains 09:30-11:00 ET')
