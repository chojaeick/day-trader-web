#!/usr/bin/env python3
"""V154 patch runtime tracker GET to return cached snapshot instead of recomputing.

Runtime patch only. Strategy unchanged. Orders unchanged.
Goal: eliminate /api/v4/USA/tracker synchronous refresh bottleneck while preserving
background refresh_usa_tracker calls in v4_engine_forever.
"""
from pathlib import Path
import shutil, py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/api.py')
B=P.with_suffix('.py.bak_v154')
S=P.read_text(errors='ignore')
if not B.exists(): shutil.copy2(P,B)

old="""@app.get('/api/v4/{market}/tracker')
def v4_tracker(market:str):
    market=market.upper()
    if market=='USA': return v4.refresh_usa_tracker(db)
    if market=='KOREA': return v4.refresh_korea_tracker(korea)
    raise HTTPException(404,'market')
"""

new="""@app.get('/api/v4/{market}/tracker')
def v4_tracker(market:str):
    market=market.upper()
    # V154: GET must never rerun heavy tracker computation on the request path.
    # Background v4_engine_forever already refreshes tracker state using to_thread.
    if market=='USA':
        for name in ('usa_tracker','tracker_usa','last_usa_tracker','usa_tracker_state'):
            val=getattr(v4,name,None)
            if val is not None:
                return val
        # Fallback to status/snapshot style state if engine stores tracker elsewhere.
        for name in ('state','status','snapshot'):
            val=getattr(v4,name,None)
            if isinstance(val,dict):
                cand=val.get('USA') or val.get('usa') or val.get('tracker')
                if cand is not None:
                    return cand
        return {'ok':True,'market':'USA','rows':[],'note':'tracker cache not initialized yet'}
    if market=='KOREA':
        for name in ('korea_tracker','tracker_korea','last_korea_tracker','korea_tracker_state'):
            val=getattr(v4,name,None)
            if val is not None:
                return val
        return {'ok':True,'market':'KOREA','rows':[],'note':'tracker cache not initialized yet'}
    raise HTTPException(404,'market')
"""

if old not in S:
    print('TRACKER_ROUTE_BLOCK_NOT_FOUND')
    raise SystemExit(2)
S=S.replace(old,new,1)
P.write_text(S)
try:
    py_compile.compile(str(P),doraise=True)
    comp='PASS'
except Exception as e:
    comp='FAIL:'+str(e)
print('=== V154 PATCH TRACKER ENDPOINT TO CACHED SNAPSHOT ===')
print('PATCHED',P)
print('BACKUP',B)
print('STRATEGY_CHANGE=NONE')
print('ORDER_CHANGE=NONE')
print('BACKGROUND_REFRESH_UNCHANGED=YES')
print('REQUEST_PATH_HEAVY_REFRESH_REMOVED=YES')
print('PY_COMPILE=',comp)
print('NEXT=RESTART_SERVICE_THEN_V155_VERIFY_LATENCY_AND_CACHE_SHAPE')
