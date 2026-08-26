#!/usr/bin/env python3
"""V164B exact runtime tick->bar audit using the runtime venv.
Read-only. No strategy/order/service mutation.
"""
from __future__ import annotations
import os, sys, json, urllib.request, traceback
from pathlib import Path

VENV_PY='/home/ubuntu/day-trader-api/venv/bin/python3'
if os.path.realpath(sys.executable) != os.path.realpath(VENV_PY):
    os.execv(VENV_PY,[VENV_PY,__file__])

print('=== V164B EXACT RUNTIME TICK->BAR PATH AUDIT (VENV) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)

sys.path.insert(0,'/home/ubuntu/day-trader-api')

try:
    import pandas as pd
    print('PANDAS_VERSION=',pd.__version__)
except Exception as e:
    print('PANDAS_IMPORT=FAIL',repr(e)); raise

try:
    from live_server import v4_engine as ve
    print('ENGINE_IMPORT=PASS')
except Exception as e:
    print('ENGINE_IMPORT=FAIL',repr(e)); traceback.print_exc(); raise SystemExit(2)

# Discover DB class/instance through the same API module used by service when possible.
try:
    from live_server import api as api_mod
    db=getattr(api_mod,'db',None)
except Exception as e:
    print('API_IMPORT=FAIL',repr(e)); db=None

if db is None:
    print('DB_INSTANCE=NONE')
    raise SystemExit(3)
print('DB_INSTANCE=',type(db).__name__)

# Runtime symbols from tracker endpoint.
def http(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=5) as r:
            return json.loads(r.read().decode('utf-8','ignore'))
    except Exception as e:
        print('HTTP_FAIL',path,repr(e)); return {}

st=http('/api/v4/USA/status')
rows=[]
if isinstance(st,dict):
    tr=st.get('tracker') or {}
    if isinstance(tr,dict): rows=tr.get('rows') or []
syms=[]
for r in rows:
    if isinstance(r,dict) and r.get('symbol'):
        s=str(r['symbol']).upper()
        if s not in syms: syms.append(s)
print('RUNTIME_SYMBOLS=',syms)

# Use exactly the engine's ticks_to_bars implementation against db.ticks(sym,40000).
for sym in syms:
    try:
        ticks=db.ticks(sym,40000)
        tlen=len(ticks) if ticks is not None else None
        print('SYMBOL',sym,'TICKS_TYPE=',type(ticks).__name__,'TICKS_LEN=',tlen)
        b1=ve.ticks_to_bars(ticks,1)
        b5=ve.ticks_to_bars(ticks,5)
        l1=len(b1) if b1 is not None else None
        l5=len(b5) if b5 is not None else None
        print('SYMBOL',sym,'B1_TYPE=',type(b1).__name__ if b1 is not None else None,'B1_LEN=',l1,'B5_LEN=',l5)
        if b1 is not None and len(b1):
            print('SYMBOL',sym,'B1_COLUMNS=',list(b1.columns))
            print('SYMBOL',sym,'B1_FIRST=',b1.iloc[0].to_dict())
            print('SYMBOL',sym,'B1_LAST=',b1.iloc[-1].to_dict())
        print('SYMBOL',sym,'B1_GE25=',bool(b1 is not None and len(b1)>=25))
    except Exception as e:
        print('SYMBOL',sym,'ERROR=',repr(e)); traceback.print_exc()

print('NEXT=IF_B1_GE25_TRUE_THEN_CTX_INPUT/SESSION_SEGMENT_AUDIT; ELSE_FIX_TICK_TO_BAR_OR_FEED_ONLY')
