#!/usr/bin/env python3
"""V151 live frozen USA telemetry observation.

READ ONLY. No strategy mutation. No broker calls.
Polls live USA endpoints several times and reports frozen evaluator visibility,
entry/exit signals, evaluator errors, and row coverage before regular session.
"""
from __future__ import annotations
import json, time, urllib.request, urllib.error

BASE='http://127.0.0.1:8000'
ENDPOINTS=['/api/v4/USA/status','/api/v4/USA/finder','/api/v4/USA/tracker']
PASSES=3
SLEEP=3

def get(path,timeout=8):
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            return r.status,json.loads(r.read().decode('utf-8','ignore'))
    except Exception as e:
        return None,{'_error':str(e)}

def rows_from(obj):
    if isinstance(obj,list): return obj
    if not isinstance(obj,dict): return []
    for k in ('rows','data','items','tracker','finder','results'):
        v=obj.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict):
            for kk in ('rows','items','data'):
                if isinstance(v.get(kk),list): return v.get(kk)
    return []

def walk(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values(): yield from walk(v)
    elif isinstance(obj,list):
        for v in obj: yield from walk(v)

print('=== V151 LIVE FROZEN TELEMETRY OBSERVATION ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE BROKER_CALLS=NONE')
all_rows=0; eval_hits=0; ctx_hits=0; errors=[]; signals=[]; endpoint_ok=0
for n in range(1,PASSES+1):
    print(f'--- PASS {n}/{PASSES} ---')
    for ep in ENDPOINTS:
        code,obj=get(ep)
        print('ENDPOINT',ep,'HTTP=',code,'ERROR=',obj.get('_error') if isinstance(obj,dict) else None)
        if code==200: endpoint_ok+=1
        rs=rows_from(obj); all_rows+=len(rs)
        if rs: print('ROWS',len(rs))
        local_eval=local_ctx=0
        for d in walk(obj):
            if 'williams_frozen_ctx' in d:
                local_ctx+=1;ctx_hits+=1
            if 'williams_frozen_eval' in d:
                local_eval+=1;eval_hits+=1
                ev=d.get('williams_frozen_eval') or {}
                sym=d.get('symbol') or d.get('ticker') or '?'
                if isinstance(ev,dict):
                    if ev.get('reason')=='ERROR' or ev.get('error'):
                        errors.append((sym,ev.get('error') or ev.get('reason')))
                    if ev.get('entry') or ev.get('exit'):
                        signals.append((sym,bool(ev.get('entry')),bool(ev.get('exit')),ev.get('reason')))
        print('FROZEN_CTX_LOCAL=',local_ctx,'FROZEN_EVAL_LOCAL=',local_eval)
    if n<PASSES: time.sleep(SLEEP)
print('=== SUMMARY ===')
print('ENDPOINT_OK_COUNT=',endpoint_ok,'/',PASSES*len(ENDPOINTS))
print('ROWS_SEEN_TOTAL=',all_rows)
print('FROZEN_CTX_HITS=',ctx_hits)
print('FROZEN_EVAL_HITS=',eval_hits)
print('FROZEN_ERRORS=',len(errors))
for x in errors[:10]: print('ERROR',x)
print('FROZEN_SIGNALS=',len(signals))
for x in signals[:20]: print('SIGNAL',x)
visible=(eval_hits>0 or ctx_hits>0)
pass_ok=bool(visible and not errors)
print('USA_FROZEN_TELEMETRY_VISIBLE=',visible)
print('V151_PASS=',pass_ok)
print('ORDER_MODE=USA_PAPER_ONLY')
print('NEXT=' + ('V152_REGULAR_SESSION_PAPER_OBSERVATION_READY' if pass_ok else 'FIX_ONLY_TELEMETRY_FEED; NO_STRATEGY_CHANGE'))
