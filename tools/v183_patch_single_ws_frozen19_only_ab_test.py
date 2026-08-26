#!/usr/bin/env python3
import os,re,shutil,subprocess,time,sqlite3
from datetime import datetime,timezone

KI='/home/ubuntu/day-trader-api/live_server/kiwoom.py'
API='/home/ubuntu/day-trader-api/live_server/api.py'
DB='/home/ubuntu/day-trader-api/daytrader.db'
FROZEN=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
print('=== V183 SINGLE WS FROZEN19-ONLY A/B TEST ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')

src=open(KI,encoding='utf-8').read()
bak=KI+'.bak_v183'
shutil.copy2(KI,bak)
print('BACKUP',bak)

# Retarget only the websocket_forever registration universe. Keep discovery/settings untouched.
# Accept current V180C merged-universe forms and replace with frozen-only tuple.
patterns=[
    r"registered\s*=\s*tuple\(dict\.fromkeys\(\[\*tuple\(getattr\(self,'frozen_paper_symbols',\(\)\) or \(\)\),\*tuple\(self\.s\.symbols\)\]\)\)",
    r"current\s*=\s*tuple\(dict\.fromkeys\(\[\*tuple\(getattr\(self,'frozen_paper_symbols',\(\)\) or \(\)\),\*tuple\(self\.s\.symbols\)\]\)\)",
]
repls=[
    "registered=tuple(getattr(self,'frozen_paper_symbols',()) or ())",
    "current=tuple(getattr(self,'frozen_paper_symbols',()) or ())",
]
counts=[]
for p,rp in zip(patterns,repls):
    src,n=re.subn(p,rp,src,count=1)
    counts.append(n)
print('PATCH_COUNTS=',counts)
if counts != [1,1]:
    print('EXPECTED_BOTH_TARGETS_ONCE; RESTORING')
    shutil.copy2(bak,KI)
    raise SystemExit(2)

open(KI,'w',encoding='utf-8').write(src)
for p in (KI,API):
    rc=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',p]).returncode
    print('PY_COMPILE',os.path.basename(p),'PASS' if rc==0 else f'FAIL rc={rc}')
    if rc:
        shutil.copy2(bak,KI); raise SystemExit(3)

# Verify no second frozen websocket task is active in api.py
api=open(API,encoding='utf-8').read()
active_second=bool(re.search(r'^[^#\n]*create_task\(k\.frozen19_websocket_forever\(\)\)',api,re.M))
print('SECOND_FROZEN_WS_ACTIVE=',active_second)
if active_second:
    print('ABORT_SECOND_WS_ACTIVE; RESTORING')
    shutil.copy2(bak,KI); raise SystemExit(4)

# restart and wait for API
rc=subprocess.run(['sudo','systemctl','restart','day-trader-api.service']).returncode
print('RESTART_RC=',rc)
ready=False
import urllib.request
for i in range(1,41):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as r:
            if r.status==200: ready=True; print('API_READY_PROBE=',i); break
    except Exception: pass
    time.sleep(1)
print('API_READY=',ready)
if not ready: raise SystemExit(5)

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
def last(sym):
    r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    return dict(r) if r else None
base={s:last(s) for s in FROZEN}
print('BASELINE_UTC=',datetime.now(timezone.utc).isoformat())
for s in FROZEN: print('BASE',s,base[s])

new=set()
for sec in (15,30,45,60,75,90):
    time.sleep(15)
    for s in FROZEN:
        cur=last(s); b=base[s]
        if cur and (not b or int(cur['id'])>int(b['id'])): new.add(s)
    print('OBSERVE_SEC=',sec,'NEW_TICK_SYMBOLS=',len(new),sorted(new))

now=datetime.now(timezone.utc); fresh=[]; stale=[]
for s in FROZEN:
    r=last(s)
    age=None
    if r:
        try: age=(now-datetime.fromisoformat(str(r['ts']).replace('Z','+00:00'))).total_seconds()
        except Exception: pass
    cls='FRESH' if age is not None and age<180 else 'STALE'
    (fresh if cls=='FRESH' else stale).append(s)
    print('SYMBOL',s,'LAST=',None if not r else r['ts'],'AGE_SEC=',None if age is None else round(age,1),'CLASS=',cls)
con.close()

jr=subprocess.run(['journalctl','-u','day-trader-api','--since','4 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20).stdout.splitlines()
print('=== RECENT WS LOGS ===')
for line in jr:
    if any(k in line for k in ['WebSocket live:','WebSocket universe refreshed:','Frozen19 WebSocket','websocket reconnect']): print(line[-1600:])

# confirm latest registration is exactly frozen19-sized if visible
regs=[x for x in jr if 'WebSocket live:' in x or 'WebSocket universe refreshed:' in x]
latest=[]
if regs:
    tail=regs[-1].split(': ',1)[-1]
    # robustly use last marker
    if 'WebSocket universe refreshed:' in regs[-1]: tail=regs[-1].split('WebSocket universe refreshed:',1)[1].strip()
    elif 'WebSocket live:' in regs[-1]: tail=regs[-1].split('WebSocket live:',1)[1].strip()
    latest=[x.split('/')[0] for x in tail.split(',') if '/' in x]
print('LATEST_REG_SYMBOLS=',latest)
print('LATEST_REG_COUNT=',len(latest))
print('FROZEN19_ONLY_REG=',latest==FROZEN)
print('FRESH_COUNT=',len(fresh),fresh)
print('STALE_COUNT=',len(stale),stale)
pass_=len(fresh)>=15 and latest==FROZEN
print('V183_FROZEN19_ONLY_AB_PASS=',pass_)
print('NEXT=IF_PASS_KEEP_FROZEN19_ONLY_WS_AND_VERIFY_COMPLETED_1M_CTX_SINGLE_EVAL; IF_FAIL_DIAGNOSE_KIWOOM_F5_SYMBOL_ELIGIBILITY/INSTRUMENT_CODES; STRATEGY_UNCHANGED')
