#!/usr/bin/env python3
"""V162 restart and verify frozen USA live context visibility.
Strategy/order logic unchanged. Restarts service, waits for API, checks DAYTRADE,
tracker latency, frozen ctx/eval visibility and reasons.
"""
from __future__ import annotations
import json, subprocess, time, urllib.request

BASE='http://127.0.0.1:8000'

def get(path,timeout=5):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            try: data=json.loads(raw)
            except Exception: data=raw
            return r.status, round(time.time()-t,3), None, data
    except Exception as e:
        return None, round(time.time()-t,3), str(e), None

def walk(obj,path='root',out=None):
    if out is None: out=[]
    if isinstance(obj,dict):
        if 'williams_frozen_ctx' in obj or 'williams_frozen_eval' in obj:
            out.append((path,obj.get('symbol'),obj.get('williams_frozen_ctx'),obj.get('williams_frozen_eval')))
        for k,v in obj.items(): walk(v,path+'.'+str(k),out)
    elif isinstance(obj,list):
        for i,v in enumerate(obj): walk(v,f'{path}[{i}]',out)
    return out

print('=== V162 RESTART + VERIFY FROZEN LIVE CTX ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')
p=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('SYSTEMCTL_RESTART_RC=',p.returncode)
ready=False
for i in range(1,21):
    c,s,e,d=get('/api/v4/runtime-mode',2)
    print('READY_PROBE',i,'HTTP=',c,'SEC=',s,'ERR=',e)
    if c==200:
        ready=True; break
    time.sleep(1)
print('API_READY=',ready)

c,s,e,mode=get('/api/v4/runtime-mode',5)
print('RUNTIME_MODE_HTTP=',c,'SEC=',s,'ERR=',e)
print('RUNTIME_MODE=',mode)
mode_daytrade=isinstance(mode,dict) and mode.get('mode')=='DAYTRADE'
print('MODE_DAYTRADE=',mode_daytrade)

hits=[]
for ep in ['/api/v4/USA/status','/api/v4/USA/tracker','/api/v4/USA/finder']:
    c,s,e,d=get(ep,5)
    print('ENDPOINT',ep,'HTTP=',c,'SEC=',s,'ERR=',e)
    if isinstance(d,dict):
        print('session=',d.get('session'),'market=',d.get('market'),'updated_at=',d.get('updated_at'))
        for item in walk(d): hits.append((ep,)+item)

ctx_hits=0; eval_hits=0; no_ctx=0; errors=0
for ep,path,sym,ctx,ev in hits:
    if ctx is not None: ctx_hits+=1
    if ev is not None:
        eval_hits+=1
        if isinstance(ev,dict):
            if ev.get('reason')=='NO_CTX': no_ctx+=1
            if ev.get('reason')=='ERROR': errors+=1
        print('FROZEN',ep,path,sym,'CTX=', 'YES' if ctx is not None else 'NO','EVAL=',ev)
print('FROZEN_CTX_HITS=',ctx_hits)
print('FROZEN_EVAL_HITS=',eval_hits)
print('FROZEN_NO_CTX=',no_ctx)
print('FROZEN_ERRORS=',errors)

try:
    st=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True).stdout.strip()
except Exception as e:
    st='UNKNOWN:'+str(e)
print('SERVICE_STATE=',st)

# Premarket can legitimately have NO_CTX because V161 refuses fake regular-session day_open.
# In REGULAR, NO_CTX must be zero for tracked USA rows if sufficient bars exist.
session=None
c,s,e,d=get('/api/v4/USA/status',5)
if isinstance(d,dict): session=d.get('session')
regular=(session=='REGULAR')
ctx_ok=(no_ctx==0 and ctx_hits>0) if regular else True
print('SESSION_REGULAR=',regular)
print('CTX_EXPECTATION_PASS=',ctx_ok)
print('V162_PASS=',bool(ready and mode_daytrade and st=='active' and errors==0 and ctx_ok))
print('NEXT=' + ('IF_PREMARKET_CONTINUE_TO_V163_BAR_HISTORY_READINESS_AUDIT' if not regular else 'V163_PAPER_EVENT_AND_EXIT_STATE_AUDIT'))
