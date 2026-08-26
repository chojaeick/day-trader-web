#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, urllib.request, json, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BASE='http://127.0.0.1:8000'
print('=== V218 CACHE ADAPTIVE FROZEN CTX WINDOW + VERIFY ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
print('PURPOSE=CACHE_SUCCESSFUL_HISTORY_WINDOW_PER_SYMBOL_AND_AVOID_REPEATED_12K_24K_40K_SEARCH')
if not API.exists(): raise SystemExit('API_NOT_FOUND')
src=API.read_text(errors='ignore')
bak=Path(str(API)+'.bak_v218'); shutil.copy2(API,bak); print('BACKUP',bak)

# Add cache next to frozen state.
state_anchor="_frozen_usa_last_bar={}"
if '_frozen_usa_ctx_window_cache' not in src:
    if state_anchor not in src: raise SystemExit('STATE_ANCHOR_NOT_FOUND')
    src=src.replace(state_anchor,state_anchor+"\n_frozen_usa_ctx_window_cache={}",1)

# Locate V217 adaptive fallback region by distinctive markers.
start=src.find("ctx=v4._v161_wire_usa_frozen_ctx(row,bars)")
if start<0: raise SystemExit('CTX_CALL_NOT_FOUND')
window=src[start:start+5000]
print('CTX_REGION_HEAD=',repr(window[:1200]))

# Replace V217 adaptive retry block if present. Keep strategy/context builder unchanged.
old_patterns=[
"""                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)
                    if not (isinstance(ctx,dict) and ctx.get('entry_args')):
                        for _lim in (12000,24000,40000):
                            if len(ticks or [])>=_lim: continue
                            _ticks=await asyncio.to_thread(db.ticks,sym,_lim)
                            _b1=await asyncio.to_thread(ticks_to_bars,_ticks,1)
                            if _b1 is None or len(_b1)<26: continue
                            _bars=_b1
                            try:
                                _last_t=_bars.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars)>26:
                                    _bars=_bars.iloc[:-1]
                            except Exception:
                                pass
                            _ctx=v4._v161_wire_usa_frozen_ctx(row,_bars)
                            if isinstance(_ctx,dict) and _ctx.get('entry_args'):
                                ticks=_ticks; b1=_b1; bars=_bars; ctx=_ctx
                                rec['ctx_fallback']=True; rec['ctx_tick_limit']=_lim; rec['bars']=len(_bars)
                                break
"""
]

new="""                    # V218: reuse the smallest previously successful history window.
                    # Context math itself is unchanged; only data-window selection is cached.
                    _cached_lim=int(_frozen_usa_ctx_window_cache.get(sym) or 0)
                    if _cached_lim and len(ticks or [])<_cached_lim:
                        _ticks=await asyncio.to_thread(db.ticks,sym,_cached_lim)
                        _b1=await asyncio.to_thread(ticks_to_bars,_ticks,1)
                        if _b1 is not None and len(_b1)>=26:
                            _bars=_b1
                            try:
                                _last_t=_bars.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars)>26:
                                    _bars=_bars.iloc[:-1]
                            except Exception:
                                pass
                            ticks=_ticks; b1=_b1; bars=_bars
                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)
                    if not (isinstance(ctx,dict) and ctx.get('entry_args')):
                        _tries=[]
                        if _cached_lim: _tries.append(_cached_lim)
                        for _lim in (12000,24000,40000):
                            if _lim not in _tries: _tries.append(_lim)
                        for _lim in _tries:
                            if len(ticks or [])>=_lim and _lim!=_cached_lim: continue
                            _ticks=await asyncio.to_thread(db.ticks,sym,_lim)
                            _b1=await asyncio.to_thread(ticks_to_bars,_ticks,1)
                            if _b1 is None or len(_b1)<26: continue
                            _bars=_b1
                            try:
                                _last_t=_bars.iloc[-1].get('time')
                                _now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                                if str(_last_t)[:16]==_now_min and len(_bars)>26:
                                    _bars=_bars.iloc[:-1]
                            except Exception:
                                pass
                            _ctx=v4._v161_wire_usa_frozen_ctx(row,_bars)
                            if isinstance(_ctx,dict) and _ctx.get('entry_args'):
                                ticks=_ticks; b1=_b1; bars=_bars; ctx=_ctx
                                _frozen_usa_ctx_window_cache[sym]=_lim
                                rec['ctx_fallback']=True; rec['ctx_tick_limit']=_lim; rec['bars']=len(_bars)
                                break
                    elif not _cached_lim:
                        # Base window already sufficient. Cache current tick count capped at 12000.
                        _frozen_usa_ctx_window_cache[sym]=min(max(len(ticks or []),2500),12000)
                    rec['ctx_window_cached']=int(_frozen_usa_ctx_window_cache.get(sym) or 0)
"""

patched=0
for old in old_patterns:
    if old in src:
        src=src.replace(old,new,1); patched=1; break
if not patched:
    # More tolerant structural replacement from first ctx call through row assignment.
    p=src.find("                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)", start-200)
    q=src.find("                    row['williams_frozen_ctx']=ctx", p)
    if p<0 or q<0: raise SystemExit('V217_BLOCK_NOT_FOUND')
    src=src[:p]+new+src[q:]
    patched=1
print('PATCHED=',patched)

API.write_text(src)
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(API)],capture_output=True,text=True)
print('PY_COMPILE_RC=',r.returncode)
if r.stderr.strip(): print(r.stderr.strip())
if r.returncode:
    shutil.copy2(bak,API); raise SystemExit('COMPILE_FAIL_RESTORED')
r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)

def get(path,t=5):
    st=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=t) as f:
            raw=f.read().decode(errors='ignore')
            try: body=json.loads(raw)
            except: body=raw
            return True,f.status,time.time()-st,body
    except Exception as e:return False,0,time.time()-st,repr(e)

ready=False
for i in range(1,31):
    ok,code,sec,body=get('/api/v4/runtime-mode',3)
    print('READY',i,'OK=',ok,'HTTP=',code,'SEC=',round(sec,3))
    if ok and code==200:
        print('RUNTIME_MODE=',body); ready=True; break
    time.sleep(2)
print('API_READY=',ready)

last_body={}
for sec_wait in (15,30,60,90):
    time.sleep(15 if sec_wait==15 else sec_wait-prev)
    prev=sec_wait
    ok,code,lat,body=get('/api/v4/USA/frozen-paper',5)
    print('OBSERVE_SEC=',sec_wait,'HTTP=',code,'LAT=',round(lat,3))
    if isinstance(body,dict):
        rows=body.get('rows') or []; last_body=body
        noctx=[r.get('symbol') for r in rows if r.get('eval_reason')=='NO_CTX']
        ctx=[r.get('symbol') for r in rows if r.get('ctx')]
        cache={r.get('symbol'):r.get('ctx_window_cached') for r in rows if r.get('ctx_window_cached')}
        print('ROWS=',len(rows),'EVAL=',body.get('evaluations'),'ERRORS=',body.get('errors'),'CTX_COUNT=',len(ctx),'NO_CTX_COUNT=',len(noctx))
        print('CACHE=',cache)
        if len(rows)==19 and not noctx and int(body.get('errors') or 0)==0 and sec_wait>=30: break

p=subprocess.run("ps -eo pid,pcpu,pmem,etime,cmd | grep '[u]vicorn live_server.api:app'",shell=True,capture_output=True,text=True)
print('UVICORN=',p.stdout.strip())
rows=(last_body.get('rows') or []) if isinstance(last_body,dict) else []
noctx=[r for r in rows if r.get('eval_reason')=='NO_CTX']
ready_ok=bool(len(rows)==19 and not noctx and int(last_body.get('errors') or 0)==0)
print('V218_ROWS19=',len(rows)==19)
print('V218_NO_CTX_COUNT=',len(noctx))
print('USA_PAPER_RUNTIME_READY=',ready_ok)
print('NEXT=IF_READY_LEAVE_RUNNING_PAPER_THROUGH_CLOSE; NO_MORE_CPU_TUNING_TONIGHT')
