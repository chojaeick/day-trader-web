#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, sqlite3, json, re, sys
from datetime import datetime, timezone

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
DB='/home/ubuntu/day-trader-api/daytrader.db'
VENV='/home/ubuntu/day-trader-api/venv/bin/python3'
PAIRS=[('AMD','ND','STALE_TEST'),('PLTR','ND','FRESH_CONTROL')]

print('=== V185 SINGLE-SYMBOL F5 A/B PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
if not RUNTIME.exists():
    print('MISSING',RUNTIME); sys.exit(2)
src=RUNTIME.read_text()
bak=RUNTIME.with_suffix('.py.bak_v185')
bak.write_text(src)
print('BACKUP',bak)

# V183 currently forces frozen19-only. Replace both registered/current assignments with a probe tuple.
patterns=[
    r"registered=tuple\(getattr\(self,'frozen_paper_symbols',\(\)\) or \(\)\)",
    r"current=tuple\(getattr\(self,'frozen_paper_symbols',\(\)\) or \(\)\)",
]

def latest(sym):
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
        return dict(r) if r else None
    finally: con.close()

def wait_api(limit=45):
    for i in range(limit):
        rc=subprocess.run(['curl','-sf','http://127.0.0.1:8000/api/v4/USA/status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode
        if rc==0: return i+1
        time.sleep(1)
    return None

results=[]
for sym,ex,label in PAIRS:
    probe=f"((\'{sym}\',))"
    patched=src
    counts=[]
    for pat in patterns:
        patched,n=re.subn(pat,lambda m: m.group(0).split('=')[0]+'='+probe,patched,count=1)
        counts.append(n)
    if counts!=[1,1]:
        print('PATCH_TARGET_FAIL',sym,counts)
        RUNTIME.write_text(src)
        sys.exit(3)
    RUNTIME.write_text(patched)
    cp=subprocess.run([VENV,'-m','py_compile',str(RUNTIME)],capture_output=True,text=True)
    print('COMPILE',sym,'PASS' if cp.returncode==0 else 'FAIL')
    if cp.returncode:
        print(cp.stderr); RUNTIME.write_text(src); sys.exit(4)
    base=latest(sym)
    print('TEST_BEGIN',label,sym,ex,'BASE',base)
    rr=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
    print('RESTART_RC',rr.returncode)
    probe_ready=wait_api()
    print('API_READY_PROBE',probe_ready)
    new=False
    seen=None
    for sec in (15,30,45,60,75,90):
        time.sleep(15)
        cur=latest(sym)
        if cur and (not base or int(cur['id'])>int(base['id'])):
            new=True; seen=cur
        print('OBSERVE_SEC',sec,'SYMBOL',sym,'NEW_TICK',new,'LATEST',cur)
        if new and sec>=30: break
    logs=subprocess.run(['journalctl','-u','day-trader-api.service','--since','3 minutes ago','--no-pager'],capture_output=True,text=True).stdout
    reg=[x for x in logs.splitlines() if 'WebSocket live:' in x or 'WebSocket universe refreshed:' in x]
    print('RECENT_REG_LOGS',sym)
    for x in reg[-4:]: print(x)
    results.append((sym,label,new,seen))

# Restore V183 frozen19-only source exactly and restart.
RUNTIME.write_text(src)
subprocess.run([VENV,'-m','py_compile',str(RUNTIME)],check=False)
rr=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
print('RESTORE_RESTART_RC',rr.returncode)
ready=wait_api()
print('RESTORE_API_READY_PROBE',ready)
print('RESULTS',results)
amd=next(x for x in results if x[0]=='AMD')
pltr=next(x for x in results if x[0]=='PLTR')
if pltr[2] and not amd[2]:
    print('V185_RESULT=SYMBOL_SPECIFIC_OR_EXCHANGE_CODE_ISSUE_CONFIRMED')
elif amd[2] and pltr[2]:
    print('V185_RESULT=GROUP_REGISTRATION_BEHAVIOR_ISSUE_CONFIRMED')
else:
    print('V185_RESULT=INCONCLUSIVE_OR_SESSION_FEED_PROBLEM')
print('RUNTIME_RESTORED_TO_V183_FROZEN19_ONLY=YES')
