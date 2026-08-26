#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V202 PATCH EXACT USA CANDIDATE BUILD OFF EVENT LOOP ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=PATCH_V201_MISSED_MULTILINE_USA_FINDER_AND_SCREENER_BLOCK')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v202')
shutil.copy2(API,bak); print('BACKUP',bak)

old="""                usa_candidates=screener_rows(db.quotes(),db.daily_metrics(),40)
                finder=v4.build_usa_finder(
                    usa_candidates,
                    k.discovery,5,db=db
                )
"""
new="""                usa_candidates=await asyncio.to_thread(lambda: screener_rows(db.quotes(),db.daily_metrics(),40))
                finder=await asyncio.to_thread(
                    lambda: v4.build_usa_finder(usa_candidates,k.discovery,5,db=db)
                )
"""
count=src.count(old)
print('EXACT_BLOCK_COUNT=',count)
if count!=1:
    pos=src.find('usa_candidates=screener_rows(db.quotes(),db.daily_metrics(),40)')
    print('EXACT_BLOCK_NOT_FOUND POS=',pos)
    if pos>=0: print('CONTEXT=',repr(src[max(0,pos-160):pos+520]))
    raise SystemExit('EXPECTED_EXACTLY_ONE_USA_CANDIDATE_BLOCK')
src=src.replace(old,new,1)
API.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stdout.strip(): print(r.stdout.strip())
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode: raise SystemExit('COMPILE_FAIL')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,timeout=4):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t,body
    except Exception as e:
        return False,0,time.time()-t,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',4)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        ready=True; print('RUNTIME_MODE=',body); break
    time.sleep(4)
print('API_READY=',ready)

lat=[]
if ready:
    for i in range(5):
        ok,code,sec,body=get('/api/v4/runtime-mode',4)
        lat.append(sec if ok else 99.0)
        print('RUNTIME_REPEAT',i+1,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        time.sleep(1)
    for ep in ('/api/v4/USA/status','/api/v4/USA/frozen-paper'):
        ok,code,sec,body=get(ep,12)
        print('ENDPOINT',ep,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
        if isinstance(body,dict) and ep.endswith('frozen-paper'):
            rows=body.get('rows') or []
            print('FROZEN_ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
            print('FROZEN_BAR_COUNT=',sum(1 for x in rows if x.get('bar')))
            print('FROZEN_CTX_COUNT=',sum(1 for x in rows if x.get('ctx')))
            for x in rows:
                print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'))

p=subprocess.run("ps -eo pid,ppid,pcpu,pmem,stat,etime,cmd --sort=-pcpu | head -10",shell=True,capture_output=True,text=True)
print('=== PROCESS TOP ===')
print(p.stdout)
fast=bool(lat and max(lat)<2.0)
print('RUNTIME_MODE_FAST=',fast)
print('V202_PASS=',bool(ready and fast))
if not ready or not fast:
    j=subprocess.run(['journalctl','-u','day-trader-api.service','-n','100','--no-pager'],capture_output=True,text=True)
    print('=== JOURNAL_LAST100 ===')
    print(j.stdout[-15000:])
print('NEXT=IF_PASS_AND_FROZEN_ROWS_19_LEAVE_RUNNING_USA_PAPER; ELSE_PATCH_ONLY_REMAINING_CONFIRMED_CPU_BLOCKER')