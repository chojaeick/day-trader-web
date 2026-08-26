#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, sqlite3, re
from datetime import datetime, timezone

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
DB='/home/ubuntu/day-trader-api/daytrader.db'
VENV='/home/ubuntu/day-trader-api/venv/bin/python'
SERVICE='day-trader-api.service'

STALE=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM']
CODES=['ND','NY','NA']

print('=== V186 STALE11 EXCHANGE-CODE BRUTEFORCE PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
orig=RUNTIME.read_text()
bak=RUNTIME.with_suffix('.py.bak_v186')
bak.write_text(orig)
print('BACKUP',bak)

# Locate the frozen19-only registration tuple currently used by V183.
pat=re.compile(r"registered\s*=\s*tuple\(getattr\(self,'frozen_paper_symbols',\(\)\)\s*or\s*\(\)\)")
if not pat.search(orig):
    print('TARGET_REGISTERED_FROZEN_ONLY_NOT_FOUND')
    raise SystemExit(2)

# refresh/current target can vary; force both registered and current to a test-only one-symbol tuple.
def latest(sym):
    con=sqlite3.connect(DB)
    con.row_factory=sqlite3.Row
    try:
        r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
        return dict(r) if r else None
    finally: con.close()

def patch_one(sym, code):
    txt=orig
    txt=pat.sub("registered=((%r),)"%sym,txt,count=1)
    # Override exchange resolution only for this symbol in _ws_items by injecting a tiny local map marker.
    marker='def _ws_items(self, symbols):'
    if marker not in txt:
        print('WS_ITEMS_DEF_NOT_FOUND'); raise SystemExit(3)
    inject=(marker+"\n        # V186 temporary one-symbol exchange override\n        _v186_exchange_override={%r:%r}\n"%(sym,code))
    txt=txt.replace(marker,inject,1)
    # Replace the first active_exchange(symbol) in _ws_items scope conservatively.
    txt=txt.replace('self.active_exchange(symbol)',"_v186_exchange_override.get(symbol,self.active_exchange(symbol))",1)
    # Freeze refresh universe to same one symbol if V183 current line exists.
    txt=re.sub(r"current\s*=\s*tuple\(getattr\(self,'frozen_paper_symbols',\(\)\)\s*or\s*\(\)\)","current=((%r),)"%sym,txt,count=1)
    RUNTIME.write_text(txt)


def compile_restart():
    cp=subprocess.run([VENV,'-m','py_compile',str(RUNTIME),str(API)],capture_output=True,text=True)
    if cp.returncode:
        print(cp.stdout,cp.stderr); return False
    rc=subprocess.run(['sudo','systemctl','restart',SERVICE]).returncode
    print('RESTART_RC',rc)
    if rc: return False
    for i in range(1,41):
        p=subprocess.run(['curl','-fsS','--max-time','2','http://127.0.0.1:8000/api/v4/USA/status'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if p.returncode==0:
            print('API_READY_PROBE',i); return True
        time.sleep(1)
    return False

results=[]
try:
    # Probe AMD all three codes first; if one succeeds, use that code pattern for all stale symbols later.
    for sym in ['AMD']:
        for code in CODES:
            RUNTIME.write_text(orig)
            patch_one(sym,code)
            print('TEST_BEGIN',sym,code,'BASE',latest(sym))
            base=latest(sym)
            if not compile_restart():
                results.append((sym,code,False,'restart_fail')); continue
            ok=False; new=None
            for sec in (15,30,45,60):
                time.sleep(15)
                cur=latest(sym)
                ok=bool(cur and (not base or cur.get('id')!=base.get('id')))
                print('OBSERVE_SEC',sec,'SYMBOL',sym,'CODE',code,'NEW_TICK',ok,'LATEST',cur)
                if ok:
                    new=cur; break
            results.append((sym,code,ok,new))
            if ok: break
finally:
    RUNTIME.write_text(orig)
    subprocess.run([VENV,'-m','py_compile',str(RUNTIME),str(API)])
    subprocess.run(['sudo','systemctl','restart',SERVICE])

print('RESULTS',results)
working=[x for x in results if x[2]]
if working:
    print('V186_RESULT=AMD_EXCHANGE_CODE_MISMATCH_CONFIRMED',working[0][1])
    print('NEXT=APPLY_DISCOVERED_CODE_AND_BATCH_PROBE_STALE11')
else:
    print('V186_RESULT=AMD_NO_F5_ON_ND_NY_NA')
    print('NEXT=PROBE_KIWOOM_SYMBOL_MASTER/JMCODE_VARIANT_OR_F5_ELIGIBILITY')
print('RUNTIME_RESTORED_TO_V183_FROZEN19_ONLY=YES')
