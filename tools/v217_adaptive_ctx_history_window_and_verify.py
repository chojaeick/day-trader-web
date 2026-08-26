#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V217 ADAPTIVE FROZEN CTX HISTORY WINDOW + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=ONLY_WHEN_CTX_MISSING_WIDEN_TICK_HISTORY_UNTIL_CURRENT_AND_PREVIOUS_REGULAR_SESSION_EXIST')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v217'); shutil.copy2(API,bak); print('BACKUP',bak)

start=src.find('async def frozen_usa_paper_forever():')
end=src.find('\nasync def ',start+10)
if start<0: raise SystemExit('FROZEN_LOOP_NOT_FOUND')
if end<0: end=len(src)
loop=src[start:end]
print('LOOP_LEN=',len(loop))

# Current loop already obtains ticks/b1 then wires ctx. Insert a fallback immediately
# after the first ctx build. Strategy/helper is untouched: we only provide enough history.
anchor="                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)\n"
print('CTX_ANCHOR_COUNT=',loop.count(anchor))
if loop.count(anchor)!=1:
    print(loop[:7000]); raise SystemExit('CTX_ANCHOR_NOT_EXACTLY_ONE')

fallback=r'''                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)
                    # V217_CTX_HISTORY_FALLBACK: do not alter Williams rules or features.
                    # If the small live window lacks the previous REGULAR session, widen
                    # only this symbol's history and rerun the exact same frozen builder.
                    ctx_history_ticks=len(ticks or [])
                    if not (isinstance(ctx,dict) and ctx.get('entry_args')):
                        for _lim in (12000,24000,40000):
                            if _lim<=ctx_history_ticks:
                                continue
                            _ticks2=await asyncio.to_thread(db.ticks,sym,_lim)
                            ctx_history_ticks=len(_ticks2 or [])
                            if not _ticks2:
                                continue
                            _b12=await asyncio.to_thread(ticks_to_bars,_ticks2,1)
                            if _b12 is None or len(_b12)<26:
                                continue
                            _bars2=_b12
                            try:
                                _last_t=_bars2.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars2)>26:
                                    _bars2=_bars2.iloc[:-1]
                            except Exception:
                                pass
                            _ctx2=v4._v161_wire_usa_frozen_ctx(row,_bars2)
                            if isinstance(_ctx2,dict) and _ctx2.get('entry_args'):
                                ctx=_ctx2
                                bars=_bars2
                                rec['bars']=len(bars)
                                rec['ctx_history_ticks']=ctx_history_ticks
                                rec['ctx_history_fallback']=True
                                break
                    rec.setdefault('ctx_history_ticks',ctx_history_ticks)
                    rec.setdefault('ctx_history_fallback',False)
'''
loop2=loop.replace(anchor,fallback,1)
src2=src[:start]+loop2+src[end:]

# Keep V216 telemetry useful; if bars field assignment isn't present before ctx, ensure it is.
API.write_text(src2)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); raise SystemExit('COMPILE_FAIL_RESTORED')

r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
BASE='http://127.0.0.1:8000'
def get(path,t=5):
    st=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=t) as f:
            raw=f.read().decode(errors='ignore')
            try:b=json.loads(raw)
            except:b=raw
            return True,f.status,time.time()-st,b
    except Exception as e:return False,0,time.time()-st,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,b=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200: ready=True; print('RUNTIME_MODE=',b); break
    time.sleep(2)
print('API_READY=',ready)

last=None
for sec in (15,30,60,90):
    time.sleep(15 if sec==15 else (sec-(15 if sec==30 else 30 if sec==60 else 60)))
    ok,code,lat,b=get('/api/v4/USA/frozen-paper',10)
    rows=(b.get('rows') or []) if isinstance(b,dict) else []
    print('OBSERVE_SEC=',sec,'HTTP=',code,'LAT=',round(lat,3),'ROWS=',len(rows),'EVAL=',b.get('evaluations') if isinstance(b,dict) else None,'ERRORS=',b.get('errors') if isinstance(b,dict) else None)
    reasons={r.get('symbol'):r.get('eval_reason') for r in rows}
    ctx=[r.get('symbol') for r in rows if r.get('ctx')]
    noctx=[r.get('symbol') for r in rows if r.get('eval_reason')=='NO_CTX']
    fb={r.get('symbol'):(r.get('ctx_history_ticks'),r.get('ctx_history_fallback'),r.get('bars')) for r in rows if r.get('ctx_history_fallback') or r.get('eval_reason')=='NO_CTX'}
    print('CTX_COUNT=',len(ctx),ctx)
    print('NO_CTX_COUNT=',len(noctx),noctx)
    print('FALLBACK_OR_NOCTX=',fb)
    last=(b,rows,ctx,noctx)
    if len(rows)==19 and not noctx: break

p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
print('UVICORN=',p.stdout.strip())
if last:
    b,rows,ctx,noctx=last
    print('V217_ROWS19=',len(rows)==19)
    print('V217_CTX_READY_COUNT=',len(ctx))
    print('V217_NO_CTX_COUNT=',len(noctx))
    print('USA_PAPER_RUNTIME_READY=',bool(len(rows)==19 and not noctx and int(b.get('errors') or 0)==0))
print('NEXT=IF_READY_KEEP_PAPER_RUNNING; IF_SOME_NO_CTX_REMAIN_DIAGNOSE_ONLY_THEIR_REGULAR_SESSION_DATE_COVERAGE')
