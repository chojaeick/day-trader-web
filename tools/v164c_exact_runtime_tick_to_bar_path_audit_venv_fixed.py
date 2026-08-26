#!/usr/bin/env python3
"""V164C exact runtime tick->bar audit using the same runtime venv.
Read-only. Forces exec into runtime python BEFORE importing pandas/runtime modules.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

VENV_PY='/home/ubuntu/day-trader-api/venv/bin/python3'
SELF=str(Path(__file__).resolve())

# Force the exact runtime interpreter before importing pandas/runtime code.
if os.path.realpath(sys.executable) != os.path.realpath(VENV_PY):
    print('REEXEC_TO_RUNTIME_PYTHON=', VENV_PY, flush=True)
    os.execv(VENV_PY, [VENV_PY, SELF])

print('=== V164C EXACT RUNTIME TICK->BAR PATH AUDIT (VENV FIXED) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=', sys.executable)

RUNTIME=Path('/home/ubuntu/day-trader-api')
if str(RUNTIME) not in sys.path:
    sys.path.insert(0,str(RUNTIME))

import pandas as pd
print('PANDAS_VERSION=', pd.__version__)
from live_server import v4_engine as ve
print('ENGINE_IMPORT=PASS')

# Obtain the same DB object class used by runtime if possible.
# v4_engine imports its DB helpers; inspect likely module-level names.
db=None
candidates=[]
for name in dir(ve):
    obj=getattr(ve,name)
    if isinstance(name,str) and ('DB' in name.upper() or 'DATABASE' in name.upper()):
        candidates.append(name)
print('DB_SYMBOL_CANDIDATES=', candidates[:30])

# Import the runtime database module directly; this is the canonical API service storage path.
from live_server.db import DB
DB_PATH='/home/ubuntu/day-trader-api/daytrader.db'
db=DB(DB_PATH)
print('DB_OPEN=PASS PATH=',DB_PATH)

# Pull current tracker symbols from local API; fallback to the observed symbols only if API unavailable.
import json, urllib.request
symbols=[]
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/tracker',timeout=5) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
    rows=d.get('rows') or [] if isinstance(d,dict) else []
    symbols=[str(x.get('symbol') or '').upper() for x in rows if isinstance(x,dict) and x.get('symbol')]
except Exception as e:
    print('TRACKER_FETCH_ERROR=',repr(e))
print('RUNTIME_SYMBOLS=',symbols)

for sym in symbols:
    try:
        ticks=db.ticks(sym,40000)
        print('SYMBOL',sym,'TICKS_TYPE=',type(ticks).__name__,'TICKS_LEN=',len(ticks) if ticks is not None else None)
        b1=ve.ticks_to_bars(ticks,1)
        b5=ve.ticks_to_bars(ticks,5)
        print('SYMBOL',sym,
              'B1_TYPE=',type(b1).__name__ if b1 is not None else None,
              'B1_LEN=',len(b1) if b1 is not None else None,
              'B5_LEN=',len(b5) if b5 is not None else None,
              'B1_GE25=',bool(b1 is not None and len(b1)>=25))
        if b1 is not None and len(b1):
            print('B1_COLUMNS=',list(b1.columns))
            print('B1_FIRST=',b1.iloc[0].to_dict())
            print('B1_LAST=',b1.iloc[-1].to_dict())
    except Exception as e:
        print('SYMBOL',sym,'ERROR=',repr(e))

print('NEXT=IF_B1_GE25_TRUE_AUDIT_CTX_SESSION_SPLIT; ELSE_FIX_RUNTIME_TICK_TO_BAR_INPUT_ONLY')
