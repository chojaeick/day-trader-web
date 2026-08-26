#!/usr/bin/env python3
"""V165B direct frozen context build audit with dynamic runtime engine class discovery.
READ ONLY. Must run with runtime venv python.
"""
from __future__ import annotations
import ast, inspect, json, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

print('=== V165B DIRECT FROZEN CTX BUILD AUDIT (DYNAMIC CLASS) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)

RUNTIME=Path('/home/ubuntu/day-trader-api')
if str(RUNTIME) not in sys.path: sys.path.insert(0,str(RUNTIME))
from live_server import v4_engine as ve

# Discover the actual class that owns the frozen ctx builder instead of assuming V4Engine.
engine_cls=None
for name,obj in vars(ve).items():
    if inspect.isclass(obj) and hasattr(obj,'_v142_build_usa_frozen_ctx') and hasattr(obj,'_usa_row'):
        engine_cls=obj
        break
print('ENGINE_CLASS=', getattr(engine_cls,'__name__',None))
if engine_cls is None:
    raise SystemExit('ENGINE_CLASS_NOT_FOUND')

# Get current runtime rows/symbols.
def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=5) as r:
        return json.loads(r.read().decode())
status=get('/api/v4/USA/status')
rows=((status.get('tracker') or {}).get('rows') or []) if isinstance(status,dict) else []
syms=[]
for r in rows:
    s=str((r or {}).get('symbol') or '').upper()
    if s and s not in syms: syms.append(s)
print('RUNTIME_SYMBOLS=',syms)

# Instantiate as runtime engine normally does, trying no-arg first; if constructor needs args,
# bypass __init__ because builder only uses module helpers + row/b1 and optional state attrs.
try:
    eng=engine_cls()
    print('ENGINE_INIT=NOARG_OK')
except Exception as e:
    print('ENGINE_INIT=NOARG_FAIL',repr(e))
    eng=engine_cls.__new__(engine_cls)
    print('ENGINE_INIT=BYPASS_NEW')

# Attach minimal state containers if V161 builder expects them.
for attr in ('_williams_frozen_cross_state','_williams_frozen_weak_state'):
    if not hasattr(eng,attr): setattr(eng,attr,{})

# Find DB class dynamically and open runtime DB.
db_cls=None
for name,obj in vars(ve).items():
    if inspect.isclass(obj) and hasattr(obj,'ticks') and hasattr(obj,'quotes'):
        db_cls=obj; break
print('DB_CLASS=',getattr(db_cls,'__name__',None))
if db_cls is None:
    raise SystemExit('DB_CLASS_NOT_FOUND')
try:
    db=db_cls('/home/ubuntu/day-trader-api/daytrader.db')
except Exception:
    try: db=db_cls()
    except Exception as e: raise SystemExit('DB_INIT_FAIL '+repr(e))

for row in rows:
    sym=str((row or {}).get('symbol') or '').upper()
    if not sym: continue
    ticks=db.ticks(sym,40000)
    b1=ve.ticks_to_bars(ticks,1)
    print('SYMBOL',sym,'TICKS=',len(ticks or []),'B1_LEN=',0 if b1 is None else len(b1))
    try:
        ctx=eng._v142_build_usa_frozen_ctx(dict(row),b1,None)
        print('SYMBOL',sym,'CTX_TYPE=',type(ctx).__name__,'CTX_READY=',bool(ctx and isinstance(ctx,dict) and ctx.get('entry_args')),'CTX=',ctx)
    except Exception as e:
        print('SYMBOL',sym,'CTX_CALL_ERROR=',repr(e))

print('NEXT=IF_CTX_READY_TRUE_WIRING_OR_SESSION_TIMING_ONLY; IF_FALSE_INSPECT_RETURN_REASON_AND_OHLC_PARTITION')
