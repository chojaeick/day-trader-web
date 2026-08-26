#!/usr/bin/env python3
"""V164 exact runtime tick->1m bar path audit for USA frozen feed readiness.
READ ONLY. Uses the same live_server imports and db.ticks/ticks_to_bars path as engine.
No service/strategy/order mutation.
"""
from __future__ import annotations
import sys, json, urllib.request, traceback
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
sys.path.insert(0,str(ROOT))

print('=== V164 EXACT RUNTIME TICK->BAR PATH AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

# Discover symbols from live status/tracker.
def get(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=5) as r:
            return json.loads(r.read().decode('utf-8','ignore'))
    except Exception as e:
        print('HTTP_ERROR',path,repr(e)); return {}

syms=[]
for ep in ['/api/v4/USA/status','/api/v4/USA/tracker']:
    d=get(ep)
    rows=[]
    if isinstance(d,dict):
        if isinstance(d.get('rows'),list): rows=d['rows']
        tr=d.get('tracker')
        if isinstance(tr,dict) and isinstance(tr.get('rows'),list): rows += tr['rows']
    for r in rows:
        s=str((r or {}).get('symbol') or '').upper()
        if s and s not in syms: syms.append(s)
print('RUNTIME_SYMBOLS=',syms)

try:
    from live_server import v4_engine as ve
    print('ENGINE_IMPORT=PASS')
    print('TICKS_TO_BARS=',ve.ticks_to_bars)
except Exception as e:
    print('ENGINE_IMPORT=FAIL',repr(e)); raise

# Find live DB object using same module/class path available at runtime.
db=None
errs=[]
for modname, attr in [
    ('live_server.db','DB'),('live_server.db','Database'),('live_server.database','DB'),
    ('live_server.database','Database'),('live_server.storage','DB')]:
    try:
        m=__import__(modname,fromlist=[attr]); C=getattr(m,attr)
        try: db=C(str(ROOT/'daytrader.db'))
        except Exception: db=C()
        if hasattr(db,'ticks'): break
    except Exception as e: errs.append((modname,attr,repr(e))); db=None

if db is None or not hasattr(db,'ticks'):
    # Last resort: inspect api module for global db instance.
    try:
        from live_server import api
        cand=getattr(api,'db',None)
        if cand is not None and hasattr(cand,'ticks'): db=cand
    except Exception as e: errs.append(('live_server.api','db',repr(e)))

print('DB_OBJECT=',type(db).__name__ if db is not None else None)
if db is None:
    print('DB_DISCOVERY_ERRORS=',errs)
    raise SystemExit(2)

for sym in syms:
    try:
        ticks=db.ticks(sym,40000)
        print('\nSYMBOL',sym)
        print('TICKS_TYPE=',type(ticks).__name__)
        try: print('TICKS_LEN=',len(ticks))
        except Exception as e: print('TICKS_LEN_ERROR=',repr(e))
        if hasattr(ticks,'columns'): print('TICKS_COLUMNS=',list(ticks.columns))
        if isinstance(ticks,list) and ticks: print('TICKS_SAMPLE_KEYS=',list((ticks[0] or {}).keys()) if isinstance(ticks[0],dict) else type(ticks[0]).__name__)
        try:
            b1=ve.ticks_to_bars(ticks,1)
            print('B1_TYPE=',type(b1).__name__)
            print('B1_IS_NONE=',b1 is None)
            if b1 is not None:
                try: print('B1_LEN=',len(b1))
                except Exception as e: print('B1_LEN_ERROR=',repr(e))
                if hasattr(b1,'columns'): print('B1_COLUMNS=',list(b1.columns))
                if hasattr(b1,'tail') and len(b1):
                    try: print('B1_LAST=',b1.tail(2).to_dict('records'))
                    except Exception as e: print('B1_LAST_ERROR=',repr(e))
        except Exception as e:
            print('TICKS_TO_BARS_ERROR=',type(e).__name__,str(e))
            traceback.print_exc(limit=1)
    except Exception as e:
        print('SYMBOL_ERROR',sym,type(e).__name__,str(e))

print('\nNEXT=IF_B1_VALID_FIX_V163_AUDIT_ONLY; IF_B1_INVALID_FIX_LIVE_FEED_PATH_ONLY; FROZEN_STRATEGY_UNCHANGED')
