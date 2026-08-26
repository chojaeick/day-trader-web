#!/usr/bin/env python3
import os, sys, time, json, subprocess, urllib.request

BASE='http://127.0.0.1:8000'
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']

print('=== V172 RESTART + VERIFY FROZEN19 FEED COVERAGE ===')
print('SERVICE_MUTATION=RESTART_ONLY STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')

rc=subprocess.run(['sudo','systemctl','restart','day-trader-api'],capture_output=True,text=True).returncode
print('RESTART_RC=',rc)

ready=False
for i in range(1,31):
    try:
        with urllib.request.urlopen(BASE+'/api/v4/USA/status',timeout=2) as r:
            if r.status==200:
                ready=True; print('API_READY_PROBE=',i); break
    except Exception:
        pass
    time.sleep(1)
print('API_READY=',ready)
if not ready:
    sys.exit(2)

# give websocket/universe loop a moment after API readiness
time.sleep(5)

def get(path):
    try:
        t=time.time()
        with urllib.request.urlopen(BASE+path,timeout=5) as r:
            data=json.loads(r.read().decode())
            return r.status, round(time.time()-t,3), data
    except Exception as e:
        return None,None,{'error':repr(e)}

for path in ['/api/v4/USA/status','/api/v4/USA/tracker','/api/universe']:
    st,dt,data=get(path)
    print('HTTP',path,'=',st,'SEC=',dt)
    if path.endswith('/status') and isinstance(data,dict):
        print('SESSION=',data.get('session'))
        tr=(data.get('tracker') or {}) if isinstance(data.get('tracker'),dict) else {}
        print('TRACKER_ROWS=',[(r.get('symbol'),r.get('state'),r.get('williams_frozen_eval',{}).get('reason') if isinstance(r.get('williams_frozen_eval'),dict) else None) for r in (tr.get('rows') or [])])
        print('PAPER_POSITIONS=',[(p.get('symbol'),p.get('qty'),p.get('avg_entry')) for p in ((data.get('paper_account') or {}).get('positions') or [])])
    if path.endswith('/universe') and isinstance(data,dict):
        rows=data.get('rows') or []
        syms=[str(r.get('symbol') or '').upper() for r in rows if isinstance(r,dict)]
        print('DISCOVERY_COUNT=',len(syms),'DISCOVERY_HEAD=',syms[:30])

# exact runtime DB inspection using runtime venv import path
runtime='/home/ubuntu/day-trader-api'
sys.path.insert(0,runtime)
try:
    from live_server.config import Settings
    from live_server.db import DB
    s=Settings(); db=DB(s.db_path)
    counts={}
    last={}
    for sym in FROZEN:
        ticks=db.ticks(sym,40000)
        counts[sym]=len(ticks or [])
        last[sym]=(ticks[-1].get('ts') if ticks else None)
    have=[s for s in FROZEN if counts[s]>0]
    zero=[s for s in FROZEN if counts[s]==0]
    print('FROZEN19_TICK_HAVE=',len(have),have)
    print('FROZEN19_TICK_ZERO=',len(zero),zero)
    for sym in FROZEN:
        print('SYMBOL',sym,'TICKS=',counts[sym],'LAST=',last[sym])
except Exception as e:
    print('DB_AUDIT_ERROR=',repr(e))
    have=[]; zero=FROZEN[:]

# inspect service logs for V171 markers / websocket universe refresh
try:
    p=subprocess.run(['journalctl','-u','day-trader-api','-n','250','--no-pager'],capture_output=True,text=True,timeout=10)
    lines=p.stdout.splitlines()
    keep=[x for x in lines if ('WebSocket universe refreshed:' in x or 'V171' in x or 'frozen' in x.lower())]
    print('=== RECENT V171/WS LOGS ===')
    for x in keep[-30:]: print(x)
except Exception as e:
    print('JOURNAL_ERROR=',repr(e))

print('FROZEN19_WS_OR_TICK_COVERAGE_PASS=', len(have)>=15)
print('NEXT=IF_COVERAGE_LOW_DIAGNOSE_WS_EXCHANGE_MAPPING; IF_HIGH_VERIFY_COMPLETED_BAR_CTX_AND_SINGLE_EVAL')
