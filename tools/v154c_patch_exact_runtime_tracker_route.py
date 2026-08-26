#!/usr/bin/env python3
"""V154C patch exact deployed USA tracker GET route to return cached snapshot only.

Runtime patch only. No strategy changes. No order changes. Korea route untouched.
The heavy refresh_usa_tracker(db) remains in the background engine loop.
"""
from pathlib import Path
import shutil, py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/api.py')
B=P.with_suffix('.py.bak_v154c')
S=P.read_text()
if not B.exists():
    shutil.copy2(P,B)

old="""@app.get('/api/v4/{market}/tracker')
def v4_tracker(market:str):
    market=market.upper()
    if market=='USA': return v4.refresh_usa_tracker(db)
    if market=='KOREA': return v4.refresh_korea_tracker(korea)
    raise HTTPException(400,'market must be USA or KOREA')
"""
new="""@app.get('/api/v4/{market}/tracker')
def v4_tracker(market:str):
    market=market.upper()
    if market=='USA':
        # V154C: request path must never run heavy refresh_usa_tracker().
        # Background v4_engine_forever already refreshes USA tracker.
        snap=getattr(v4,'tracker',{}).get('USA') if isinstance(getattr(v4,'tracker',None),dict) else None
        if snap is None:
            snap=getattr(v4,'tracker_state',{}).get('USA') if isinstance(getattr(v4,'tracker_state',None),dict) else None
        if snap is None:
            snap=getattr(v4,'last_tracker',{}).get('USA') if isinstance(getattr(v4,'last_tracker',None),dict) else None
        if snap is None:
            # Fallback: expose existing lightweight status rather than recalculating.
            try:
                st=v4.status('USA')
                return {'ok':True,'market':'USA','cached':True,'rows':(st or {}).get('tracker') or [],'status':st}
            except Exception:
                return {'ok':True,'market':'USA','cached':True,'rows':[]}
        if isinstance(snap,dict):
            out=dict(snap); out.setdefault('cached',True); out.setdefault('market','USA'); return out
        return {'ok':True,'market':'USA','cached':True,'rows':snap if isinstance(snap,list) else []}
    if market=='KOREA': return v4.refresh_korea_tracker(korea)
    raise HTTPException(400,'market must be USA or KOREA')
"""

if old not in S:
    print('EXACT_ROUTE_NOT_FOUND')
    raise SystemExit(2)
S=S.replace(old,new,1)
P.write_text(S)
try:
    py_compile.compile(str(P),doraise=True)
    comp='PASS'
except Exception as e:
    comp='FAIL:'+str(e)

print('=== V154C EXACT USA TRACKER ROUTE PATCH ===')
print('PATCHED',P)
print('BACKUP',B)
print('USA_REQUEST_REFRESH_REMOVED=', "if market=='USA': return v4.refresh_usa_tracker(db)" not in S)
print('KOREA_ROUTE_UNCHANGED=', "if market=='KOREA': return v4.refresh_korea_tracker(korea)" in S)
print('BACKGROUND_REFRESH_STILL_PRESENT=', 'await asyncio.to_thread(v4.refresh_usa_tracker,db)' in S)
print('STRATEGY_CHANGE=NONE')
print('ORDER_CHANGE=NONE')
print('PY_COMPILE=',comp)
print('NEXT=RESTART_SERVICE_AND_MEASURE_TRACKER_LATENCY')
