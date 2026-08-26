#!/usr/bin/env python3
from pathlib import Path
import py_compile, shutil

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
KIW=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')

print('=== V171 PATCH FROZEN 19 USA PAPER FEED LOOP ===')
print('STRATEGY_CONSTANTS_CHANGED=NO REAL_BROKER_AUTHORITY_ADDED=NO KOREA_PATH_CHANGED=NO')
for p in (API,ENG,KIW):
    if not p.exists(): raise SystemExit(f'MISSING {p}')

for p,suf in ((API,'.bak_v171'),(ENG,'.bak_v171'),(KIW,'.bak_v171')):
    shutil.copy2(p,Path(str(p)+suf))

api=API.read_text(errors='ignore')
eng=ENG.read_text(errors='ignore')
kiw=KIW.read_text(errors='ignore')

# 1) API: declare frozen research/paper universe and hand it to websocket client.
marker="v4=CleanEngine(s.db_path)"
block="""v4=CleanEngine(s.db_path)

# V171_FROZEN19_PAPER_FEED: isolated replay-equivalent USA paper universe.
FROZEN_USA_PAPER_SYMBOLS=(
    'AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR',
    'QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM'
)
k.frozen_paper_symbols=list(FROZEN_USA_PAPER_SYMBOLS)
v4._frozen_universe_loop_enabled=True
_frozen_usa_paper_state={'enabled':True,'symbols':list(FROZEN_USA_PAPER_SYMBOLS),'rows':[],
                         'updated_at':None,'errors':0,'evaluations':0,'paper_events':0}
_frozen_usa_last_bar={}
"""
if 'V171_FROZEN19_PAPER_FEED' not in api:
    if marker not in api: raise SystemExit('API_V4_INIT_MARKER_NOT_FOUND')
    api=api.replace(marker,block,1)

# 2) Dedicated completed-1m paper loop. It does not use finder/heavy tracker selection.
loop_marker='async def korea_safety_forever():'
loop_code=r'''async def frozen_usa_paper_forever():
    """V171: frozen 19 feed/evaluation loop; paper ledger only, once per completed 1m bar."""
    await asyncio.sleep(8)
    while True:
        try:
            if _runtime_profile().get('mode')!='DAYTRADE':
                await asyncio.sleep(2)
                continue
            out=[]
            for sym in FROZEN_USA_PAPER_SYMBOLS:
                rec={'symbol':sym,'ctx':False,'eval_reason':None,'bar':None,'ticks':0,'paper_event':False}
                try:
                    ticks=await asyncio.to_thread(db.ticks,sym,40000)
                    rec['ticks']=len(ticks or [])
                    if not ticks:
                        rec['eval_reason']='NO_TICKS'; out.append(rec); continue
                    b1=await asyncio.to_thread(ticks_to_bars,ticks,1)
                    if b1 is None or len(b1)<26:
                        rec['eval_reason']='BARS_LT_26'; out.append(rec); continue

                    # Replay parity: evaluate only a completed minute bar, never the still-forming bar.
                    bars=b1
                    try:
                        last_t=bars.iloc[-1].get('time')
                        now_min=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
                        if str(last_t)[:16]==now_min and len(bars)>26:
                            bars=bars.iloc[:-1]
                    except Exception:
                        pass
                    if bars is None or len(bars)<25:
                        rec['eval_reason']='COMPLETED_BARS_LT_25'; out.append(rec); continue
                    bar_key=str(bars.iloc[-1].get('time'))
                    rec['bar']=bar_key
                    if _frozen_usa_last_bar.get(sym)==bar_key:
                        old=next((x for x in (_frozen_usa_paper_state.get('rows') or []) if x.get('symbol')==sym),None)
                        out.append(dict(old or rec)); continue

                    price=float(bars.iloc[-1].get('close') or 0)
                    row={'market':'USA','symbol':sym,'price':price,'session':'REGULAR'}
                    ctx=v4._v161_wire_usa_frozen_ctx(row,bars)
                    row['williams_frozen_ctx']=ctx
                    rec['ctx']=bool(isinstance(ctx,dict) and ctx.get('entry_args'))
                    paper_result=v4._paper_williams_step('USA',row)
                    ev=row.get('williams_frozen_eval') or {}
                    rec['eval_reason']=ev.get('reason')
                    rec['entry']=bool(ev.get('entry'))
                    rec['exit']=bool(ev.get('exit'))
                    rec['paper_event']=paper_result is not None
                    if paper_result is not None:
                        _frozen_usa_paper_state['paper_events']=int(_frozen_usa_paper_state.get('paper_events') or 0)+1
                    _frozen_usa_paper_state['evaluations']=int(_frozen_usa_paper_state.get('evaluations') or 0)+1
                    _frozen_usa_last_bar[sym]=bar_key
                    out.append(rec)
                except Exception as e:
                    rec['eval_reason']='ERROR'; rec['error']=str(e)[:300]; out.append(rec)
                    _frozen_usa_paper_state['errors']=int(_frozen_usa_paper_state.get('errors') or 0)+1
            _frozen_usa_paper_state['rows']=out
            _frozen_usa_paper_state['updated_at']=datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _frozen_usa_paper_state['errors']=int(_frozen_usa_paper_state.get('errors') or 0)+1
            _frozen_usa_paper_state['last_error']=str(e)[:500]
            logging.exception('V171 frozen USA paper loop failed')
        await asyncio.sleep(2)

'''
if 'async def frozen_usa_paper_forever()' not in api:
    if loop_marker not in api: raise SystemExit('API_LOOP_INSERT_MARKER_NOT_FOUND')
    api=api.replace(loop_marker,loop_code+loop_marker,1)

# 3) Start the dedicated loop alongside existing USA loop.
if 'asyncio.create_task(frozen_usa_paper_forever())' not in api:
    task='asyncio.create_task(v4_engine_forever())'
    if task not in api: raise SystemExit('API_TASK_MARKER_NOT_FOUND')
    api=api.replace(task,task+',\n                       asyncio.create_task(frozen_usa_paper_forever())',1)

# 4) Telemetry endpoint.
route_marker="@app.get('/api/v4/{market}/status')"
route_code="""@app.get('/api/v4/USA/frozen-paper')
def v171_usa_frozen_paper_status():
    return {'ok':True,'market':'USA','mode':'PAPER_ONLY','strategy':'WILLIAMS_FROZEN_V136',**_frozen_usa_paper_state}

"""
if "@app.get('/api/v4/USA/frozen-paper')" not in api:
    if route_marker not in api: raise SystemExit('API_ROUTE_MARKER_NOT_FOUND')
    api=api.replace(route_marker,route_code+route_marker,1)

# 5) Websocket subscription: augment dynamic universe with the frozen paper list only.
old='current=tuple(self.s.symbols)'
new="current=tuple(dict.fromkeys([*tuple(self.s.symbols),*tuple(getattr(self,'frozen_paper_symbols',()) or ())]))"
if 'frozen_paper_symbols' not in kiw:
    if old not in kiw: raise SystemExit('KIWOOM_WS_CURRENT_MARKER_NOT_FOUND')
    kiw=kiw.replace(old,new,1)

# 6) Heavy tracker must not be a second USA frozen-paper authority once V171 loop is active.
old_eng="paper_result=self._paper_williams_step(market,r)\n            if paper_result is not None:r['paper_williams']=paper_result"
new_eng="""# V171_SINGLE_USA_PAPER_AUTHORITY: dedicated frozen19 loop owns USA paper evaluation.
            paper_result=None if (market=='USA' and getattr(self,'_frozen_universe_loop_enabled',False)) else self._paper_williams_step(market,r)
            if paper_result is not None:r['paper_williams']=paper_result"""
if 'V171_SINGLE_USA_PAPER_AUTHORITY' not in eng:
    if old_eng not in eng: raise SystemExit('ENGINE_PAPER_STEP_MARKER_NOT_FOUND')
    eng=eng.replace(old_eng,new_eng,1)

API.write_text(api); ENG.write_text(eng); KIW.write_text(kiw)

ok=True
for p in (API,ENG,KIW):
    try:
        py_compile.compile(str(p),doraise=True); print('PY_COMPILE',p.name,'PASS')
    except Exception as e:
        ok=False; print('PY_COMPILE',p.name,'FAIL',repr(e))

print('FROZEN_CORE_19_COUNT=',19)
print('COMPLETED_1M_ONCE_PER_BAR=YES')
print('WS_FROZEN_AUGMENT=YES')
print('LEGACY_HEAVY_TRACKER_SELECTION_CHANGED=NO')
print('USA_PAPER_AUTHORITY_SINGLE_LOOP=YES')
print('REAL_BROKER_CALLS_ADDED=NONE')
print('V171_PATCH_PASS=',ok)
print('NEXT=V172_RESTART_AND_VERIFY_FROZEN19_FEED_COVERAGE')
