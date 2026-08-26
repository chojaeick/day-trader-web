#!/usr/bin/env python3
"""V164D exact runtime tick->bar audit. Run ONLY with runtime venv python.
READ ONLY. No strategy/order/service mutation.
"""
from __future__ import annotations
import sys, json, urllib.request
from pathlib import Path

print('=== V164D EXACT RUNTIME TICK->BAR PATH AUDIT (DIRECT VENV) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)

if '/home/ubuntu/day-trader-api/venv/bin/python' not in sys.executable:
    print('FATAL=NOT_RUNTIME_VENV')
    print('RUN_WITH=/home/ubuntu/day-trader-api/venv/bin/python3 tools/v164d_exact_runtime_tick_to_bar_path_direct_venv.py')
    raise SystemExit(2)

sys.path.insert(0,'/home/ubuntu/day-trader-api')
from live_server import v4_engine as ve
from live_server.db import DB

BASE='http://127.0.0.1:8000'
def get(path):
    try:
        with urllib.request.urlopen(BASE+path,timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print('HTTP_ERR',path,repr(e)); return {}

status=get('/api/v4/USA/status')
tracker=(status.get('tracker') or {}) if isinstance(status,dict) else {}
rows=tracker.get('rows') or []
syms=[]
for r in rows:
    s=str((r or {}).get('symbol') or '').upper()
    if s and s not in syms: syms.append(s)
print('RUNTIME_SYMBOLS=',syms)

db=DB('/home/ubuntu/day-trader-api/daytrader.db')
for sym in syms:
    try:
        ticks=db.ticks(sym,40000)
        print('SYMBOL',sym,'TICKS_TYPE=',type(ticks).__name__,'TICKS_LEN=',len(ticks) if ticks is not None else None)
        b1=ve.ticks_to_bars(ticks,1)
        b5=ve.ticks_to_bars(ticks,5)
        print('SYMBOL',sym,'B1_TYPE=',type(b1).__name__,'B1_LEN=',len(b1) if b1 is not None else None,'B1_GE25=',bool(b1 is not None and len(b1)>=25),'B5_LEN=',len(b5) if b5 is not None else None)
        if b1 is not None and len(b1):
            print('SYMBOL',sym,'B1_COLS=',list(b1.columns))
            print('SYMBOL',sym,'B1_FIRST_TIME=',str(b1.iloc[0].get('time')),'B1_LAST_TIME=',str(b1.iloc[-1].get('time')))
    except Exception as e:
        print('SYMBOL',sym,'ERROR=',type(e).__name__,str(e))
print('NEXT=IF_B1_GE25_TRUE_FIX_CTX_SESSION_PARTITION_OR_UNIVERSE; ELSE_FIX_TICK_TO_BAR_INPUT_PATH')
