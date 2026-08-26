#!/usr/bin/env python3
import subprocess, time, urllib.request, json

BASE='http://127.0.0.1:8000'
print('=== V198B POST-RESTART READINESS CHECK ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

r=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True)
print('SERVICE_STATE=',(r.stdout or r.stderr).strip(),'RC=',r.returncode)

j=subprocess.run(['journalctl','-u','day-trader-api.service','--since','5 min ago','--no-pager'],capture_output=True,text=True)
lines=(j.stdout or '').splitlines()
for line in lines[-120:]:
    if any(x in line for x in ('Started server process','Application startup complete','Uvicorn running','WebSocket live:','Traceback','ERROR','ValueError','ConnectionClosedError')):
        print('JOURNAL',line)

def get(path,timeout=5):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode(errors='ignore')
            try:d=json.loads(raw)
            except Exception:d=raw
            return True,r.status,round(time.time()-t,3),d
    except Exception as e:
        return False,0,round(time.time()-t,3),repr(e)

for i in range(1,7):
    ok,code,sec,d=get('/api/v4/runtime-mode',timeout=5)
    print('PROBE',i,'RUNTIME_MODE',ok,code,sec,d if isinstance(d,str) else d)
    if ok: break
    time.sleep(10)

for ep in ('/api/v4/USA/frozen-paper','/api/v4/USA/status'):
    ok,code,sec,d=get(ep,timeout=10)
    print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',sec)
    if isinstance(d,dict):
        if ep.endswith('frozen-paper'):
            rows=d.get('rows') or []
            print(' FROZEN_ROWS=',len(rows),'EVAL=',d.get('evaluations'),'ERRORS=',d.get('errors'),'PAPER_EVENTS=',d.get('paper_events'),'UPDATED=',d.get('updated_at'))
            for r in rows:
                print('  ROW',r.get('symbol'),'BAR',r.get('bar'),'CTX',r.get('ctx'),'REASON',r.get('eval_reason'),'TICKS',r.get('ticks'))
        else:
            print(' STATUS_MODE=',d.get('mode'),'SESSION=',d.get('session'),'POSITIONS=',len(d.get('positions') or []),'PAPER_TRADES=',len(d.get('paper_trades') or []))
    else:
        print(' BODY=',d)

print('NEXT=IF_RUNTIME_AND_FROZEN_ENDPOINT_RESPOND_WITH_CURRENT_ROWS__USA_PAPER_RUNTIME_READY; ELSE_DIAGNOSE_ONLY_CURRENT_STARTUP_BLOCKER')
