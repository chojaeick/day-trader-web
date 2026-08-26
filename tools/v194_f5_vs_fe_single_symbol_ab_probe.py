#!/usr/bin/env python3
import os, re, time, json, sqlite3, shutil, subprocess, urllib.request
from datetime import datetime, timezone

RUNTIME='/home/ubuntu/day-trader-api/live_server/kiwoom.py'
DB='/home/ubuntu/day-trader-api/daytrader.db'
BACKUP=RUNTIME+'.bak_v194'
TESTS=[('AMD','F5'),('AMD','FE'),('NVDA','F5'),('NVDA','FE')]

print('=== V194 F5 VS FE SINGLE-SYMBOL A/B PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=COMPARE_KIWOOM_USA_REALTIME_TYPES_F5_VS_FE')

if not os.path.exists(RUNTIME):
    raise SystemExit('RUNTIME_NOT_FOUND')
orig=open(RUNTIME,encoding='utf-8').read()
shutil.copy2(RUNTIME,BACKUP)
print('BACKUP',BACKUP)

reg_pat="registered=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
cur_pat="current=tuple(getattr(self,'frozen_paper_symbols',()) or ())"
filter_pat="if str(row.get('type')) != 'F5': continue"
print('REGISTERED_COUNT=',orig.count(reg_pat),'CURRENT_COUNT=',orig.count(cur_pat),'FILTER_COUNT=',orig.count(filter_pat))
if orig.count(reg_pat)!=1 or orig.count(cur_pat)!=1 or orig.count(filter_pat)!=1:
    raise SystemExit('EXPECTED_RUNTIME_TARGETS_NOT_FOUND')

def restart_ready():
    rc=subprocess.run(['sudo','systemctl','restart','day-trader-api'],capture_output=True,text=True).returncode
    print('RESTART_RC',rc)
    for i in range(1,46):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as r:
                if r.status==200:
                    print('API_READY_PROBE',i); return True
        except Exception:
            pass
        time.sleep(2)
    print('API_READY=False'); return False

def last_tick(sym):
    con=sqlite3.connect(DB)
    r=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    con.close(); return r

def raw_hits(sym,typ,since):
    con=sqlite3.connect(DB)
    rows=con.execute('select id,payload,ts from raw_ws where ts>=? order by id',(since,)).fetchall()
    con.close()
    hits=[]
    shapes={}
    for rid,payload,ts in rows:
        try: d=json.loads(payload)
        except Exception: continue
        tr=str(d.get('trnm') or '')
        shapes[tr]=shapes.get(tr,0)+1
        for row in d.get('data') or []:
            if not isinstance(row,dict): continue
            item=str(row.get('item') or '').upper()
            rt=str(row.get('type') or '')
            if item==sym and rt==typ:
                hits.append((rid,ts,rt,item,row.get('values')))
    return hits,shapes

results=[]
try:
    for sym,typ in TESTS:
        patched=orig.replace(reg_pat,f"registered=({sym!r},)")
        patched=patched.replace(cur_pat,f"current=({sym!r},)")
        patched=patched.replace("'type':['F5']",f"'type':['{typ}']")
        patched=patched.replace(filter_pat,f"if str(row.get('type')) != '{typ}': continue")
        open(RUNTIME,'w',encoding='utf-8').write(patched)
        cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',RUNTIME],capture_output=True,text=True)
        print('COMPILE',sym,typ,'PASS' if cp.returncode==0 else 'FAIL',cp.stderr.strip())
        if cp.returncode!=0: results.append((sym,typ,False,False,'COMPILE_FAIL')); continue
        base=last_tick(sym); since=datetime.now(timezone.utc).isoformat()
        print('TEST_BEGIN',sym,typ,'BASE',base,'SINCE',since)
        if not restart_ready():
            results.append((sym,typ,False,False,'API_NOT_READY')); continue
        new=False; latest=None
        for sec in (20,40,60,80,100,120):
            time.sleep(20)
            latest=last_tick(sym)
            new=bool(latest and (base is None or latest[0]!=base[0]))
            print('OBSERVE_SEC',sec,'SYMBOL',sym,'TYPE',typ,'NEW_TICK',new,'LATEST',latest)
            if new: break
        hits,shapes=raw_hits(sym,typ,since)
        print('RAW_SHAPES',sym,typ,shapes)
        print('RAW_HITS',sym,typ,'COUNT',len(hits))
        for h in hits[:3]: print('RAW_SAMPLE',sym,typ,h)
        results.append((sym,typ,new,bool(hits),latest))
finally:
    open(RUNTIME,'w',encoding='utf-8').write(orig)
    cp=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',RUNTIME],capture_output=True,text=True)
    print('RESTORE_COMPILE','PASS' if cp.returncode==0 else 'FAIL')
    restart_ready()

print('RESULTS',results)
for sym in ('AMD','NVDA'):
    sub=[x for x in results if x[0]==sym]
    print('SYMBOL_RESULT',sym,sub)
print('RUNTIME_RESTORED=YES')
print('NEXT=IF_AMD_FE_DELIVERS_AND_F5_DOES_NOT_PATCH_FROZEN_FEED_TYPE/PARSER_WITH_PARITY_AUDIT; IF_NEITHER_DELIVERS_KEEP_SYMBOL_SPECIFIC_F5_ELIGIBILITY_DIAGNOSIS')
