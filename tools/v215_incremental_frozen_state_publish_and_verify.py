#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V215 INCREMENTAL FROZEN19 STATE PUBLISH + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=PUBLISH_EACH_SYMBOL_ROW_IMMEDIATELY_INSTEAD_OF_WAITING_FOR_FULL_19_SYMBOL_SWEEP')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
bak=Path(str(API)+'.bak_v215'); shutil.copy2(API,bak); print('BACKUP',bak)
src=API.read_text(errors='ignore')

# Work only inside frozen_usa_paper_forever(). Preserve all strategy/evaluation logic.
start=src.find('async def frozen_usa_paper_forever():')
if start<0: raise SystemExit('FROZEN_LOOP_NOT_FOUND')
end=src.find('\nasync def ', start+10)
if end<0: end=src.find('\n@', start+10)
if end<0: raise SystemExit('FROZEN_LOOP_END_NOT_FOUND')
loop=src[start:end]
print('LOOP_LEN=',len(loop))

# Add helper immediately after out=[] to publish rows incrementally.
anchor='            out=[]\n'
helper="""            out=[]
            # V215_INCREMENTAL_STATE: keep endpoint/trading telemetry live while
            # the 19-symbol sweep is still running. Strategy evaluation is unchanged.
            def _v215_publish(rec):
                sym=str((rec or {}).get('symbol') or '').upper()
                current=list(_frozen_usa_paper_state.get('rows') or [])
                by={str(x.get('symbol') or '').upper():dict(x) for x in current if isinstance(x,dict) and x.get('symbol')}
                if sym:
                    by[sym]=dict(rec)
                ordered=[]
                for s0 in FROZEN_USA_PAPER_SYMBOLS:
                    if s0 in by:
                        ordered.append(by[s0])
                _frozen_usa_paper_state['rows']=ordered
                _frozen_usa_paper_state['updated_at']=datetime.now(timezone.utc).isoformat()
"""
if 'V215_INCREMENTAL_STATE' not in loop:
    if loop.count(anchor)!=1: raise SystemExit(f'OUT_ANCHOR_COUNT={loop.count(anchor)}')
    loop=loop.replace(anchor,helper,1)

# Every path that appends rec/old should publish immediately afterward.
# Exact replacements are restricted to this function only.
repls=[
    ("rec['eval_reason']='NO_TICKS'; out.append(rec); continue",
     "rec['eval_reason']='NO_TICKS'; out.append(rec); _v215_publish(rec); continue"),
    ("rec['eval_reason']='BARS_LT_26'; out.append(rec); continue",
     "rec['eval_reason']='BARS_LT_26'; out.append(rec); _v215_publish(rec); continue"),
    ("rec['eval_reason']='COMPLETED_BARS_LT_25'; out.append(rec); continue",
     "rec['eval_reason']='COMPLETED_BARS_LT_25'; out.append(rec); _v215_publish(rec); continue"),
    ("out.append(dict(old or rec)); continue",
     "out.append(dict(old or rec)); _v215_publish(dict(old or rec)); continue"),
    ("                    out.append(rec)\n                except Exception as e:",
     "                    out.append(rec)\n                    _v215_publish(rec)\n                except Exception as e:"),
    ("rec['eval_reason']='ERROR'; rec['error']=str(e)[:300]; out.append(rec)",
     "rec['eval_reason']='ERROR'; rec['error']=str(e)[:300]; out.append(rec); _v215_publish(rec)"),
]
counts=[]
for old,new in repls:
    c=loop.count(old); counts.append((old[:45],c))
    if c:
        loop=loop.replace(old,new)
print('PATCH_COUNTS=',counts)

# Require the key success-path append and at least two early-exit paths to be patched.
if 'V215_INCREMENTAL_STATE' not in loop or '_v215_publish(rec)' not in loop:
    raise SystemExit('SAFE_PATCH_NOT_ESTABLISHED')

src2=src[:start]+loop+src[end:]
API.write_text(src2)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); raise SystemExit('COMPILE_FAIL_RESTORED')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,t=3):
    t0=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=t) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except Exception: body=raw
            return True,f.status,time.time()-t0,body
    except Exception as e:return False,0,time.time()-t0,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)

best=0; last=None
if ready:
    for wait in (5,15,30,45,60,90):
        if wait>5: time.sleep(wait-prev)
        prev=wait
        ok,code,sec,body=get('/api/v4/USA/frozen-paper',5)
        if isinstance(body,dict):
            rows=body.get('rows') or []
            best=max(best,len(rows)); last=body
            print('OBSERVE_SEC=',wait,'HTTP=',code,'SEC=',round(sec,3),'ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'PAPER_EVENTS=',body.get('paper_events'))
            print('SYMBOLS=',[x.get('symbol') for x in rows])
            print('REASONS=',{x.get('symbol'):x.get('eval_reason') for x in rows})
            if len(rows)>=19: break
        else:
            print('OBSERVE_SEC=',wait,'OK=',ok,'HTTP=',code,'BODY=',body)

p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
print('UVICORN=',p.stdout.strip())
print('V215_INCREMENTAL_ROWS_PASS=',best>0)
print('USA_PAPER_RUNTIME_READY=',best>=19 and isinstance(last,dict) and int(last.get('errors') or 0)==0)
print('NEXT=IF_ROWS_19_START_PAPER_RUNTIME; IF_ROWS_PARTIAL_OPTIMIZE_ONLY_SLOW_SYMBOL_SWEEP_WITHOUT_STRATEGY_CHANGE')
