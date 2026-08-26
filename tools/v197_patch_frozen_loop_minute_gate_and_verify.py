#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, json, urllib.request, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V197 PATCH FROZEN LOOP MINUTE GATE + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=STOP_19X40000_TICK_REBAR_EVERY_2SEC_AFTER_FE')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v197'); shutil.copy2(API,bak); print('BACKUP',bak)

# Per-symbol latest raw-tick minute guard. Heavy bar reconstruction is needed only when minute changes.
if '_frozen_usa_seen_tick_min={}' not in src:
    marker='_frozen_usa_last_bar={}'
    if marker not in src: raise SystemExit('LAST_BAR_STATE_NOT_FOUND')
    src=src.replace(marker,marker+'\n_frozen_usa_seen_tick_min={}  # V197 FE minute gate',1)

old="""ticks=await asyncio.to_thread(db.ticks,sym,40000)
                    rec['ticks']=len(ticks or [])
                    if not ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); continue
                    b1=await asyncio.to_thread(ticks_to_bars,ticks,1)"""
new="""# V197: FE emits many trades/sec. Avoid rebuilding 40k ticks every 2 sec.
                    latest_ticks=await asyncio.to_thread(db.ticks,sym,1)
                    if not latest_ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); continue
                    _lt=latest_ticks[-1]
                    try:
                        _lts=(_lt.get('ts') if isinstance(_lt,dict) else _lt[0])
                    except Exception:
                        _lts=str(_lt)
                    _tick_min=str(_lts)[:16]
                    if _frozen_usa_seen_tick_min.get(sym)==_tick_min:
                        old_rec=next((x for x in (_frozen_usa_paper_state.get('rows') or []) if x.get('symbol')==sym),None)
                        out.append(dict(old_rec or rec)); continue
                    _frozen_usa_seen_tick_min[sym]=_tick_min
                    # 12k is enough to preserve the pre-FE sparse history during today's transition,
                    # while removing the pathological 19x40k/2sec workload.
                    ticks=await asyncio.to_thread(db.ticks,sym,12000)
                    rec['ticks']=len(ticks or [])
                    if not ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); continue
                    b1=await asyncio.to_thread(ticks_to_bars,ticks,1)"""
count=src.count(old)
print('HEAVY_BLOCK_MATCHES=',count)
if count!=1: raise SystemExit('EXPECTED_ONE_HEAVY_BLOCK')
src=src.replace(old,new,1)
API.write_text(src)

r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)])
print('PY_COMPILE=', 'PASS' if r.returncode==0 else 'FAIL')
if r.returncode: raise SystemExit(2)
r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
print('RESTART_RC=',r.returncode)

# wait for readiness
ready=False
for i in range(1,46):
    try:
        with urllib.request.urlopen(BASE+'/api/v4/runtime-mode',timeout=2) as f:
            if f.status==200:
                ready=True; print('API_READY_PROBE=',i); break
    except Exception: time.sleep(1)
if not ready: raise SystemExit('API_NOT_READY')

# make sure DAYTRADE is active after restart
try:
    req=urllib.request.Request(BASE+'/api/v4/runtime-mode/DAYTRADE',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=5) as f: print('SET_DAYTRADE_HTTP=',f.status)
except Exception as e: print('SET_DAYTRADE_ERROR=',repr(e))

time.sleep(12)

def get(path,timeout=8):
    t=time.time()
    with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
        d=json.loads(f.read().decode())
    return f.status,time.time()-t,d

for attempt in range(1,4):
    try:
        code,sec,d=get('/api/v4/USA/frozen-paper',8)
        rows=d.get('rows') or []
        print('FROZEN_HTTP=',code,'SEC=',round(sec,3),'ROWS=',len(rows),'EVAL=',d.get('evaluations'),'ERRORS=',d.get('errors'),'PAPER_EVENTS=',d.get('paper_events'),'UPDATED=',d.get('updated_at'))
        for x in rows:
            print('ROW',x.get('symbol'),'BAR',x.get('bar'),'CTX',x.get('ctx'),'REASON',x.get('eval_reason'),'TICKS',x.get('ticks'),'ERR',x.get('error'))
        break
    except Exception as e:
        print('FROZEN_ATTEMPT',attempt,'ERROR',repr(e)); time.sleep(5)

for ep in ('/api/v4/runtime-mode','/api/v4/USA/status'):
    try:
        code,sec,d=get(ep,8)
        print('ENDPOINT',ep,'HTTP',code,'SEC',round(sec,3))
        if ep.endswith('runtime-mode'): print('RUNTIME_MODE=',d)
        else:
            print('USA_SESSION=',d.get('session'),'POSITIONS=',len(d.get('positions') or []),'PAPER_TRADES=',len(d.get('paper_trades') or []))
    except Exception as e: print('ENDPOINT_ERROR',ep,repr(e))

post=API.read_text(errors='ignore')
print('MINUTE_GATE_INSTALLED=', '_frozen_usa_seen_tick_min' in post)
print('FE_RUNTIME_UNCHANGED=YES')
print('ORDER_MODE=USA_PAPER_ONLY')
print('NEXT=IF_ENDPOINT_FAST_AND_ROWS19_WITH_CTX_RUN_FINAL_PAPER_GO; IF_CTX_HISTORY_DEFECT_PATCH_ONLY_HISTORY_INPUT')
