#!/usr/bin/env python3
"""V165 direct frozen context build audit using the exact runtime venv + engine functions.
READ ONLY. No strategy/order/service mutation.

Goal:
- For current USA tracker symbols, use the same runtime DB ticks and ticks_to_bars.
- Reconstruct the minimal USA row fields needed by V161/V142.
- Inspect ET session partitioning and previous-regular-session OHLC readiness.
- Directly call _v142_build_usa_frozen_ctx() and report why ctx is/isn't buildable.
"""
from __future__ import annotations
import sys, json, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

RUNTIME='/home/ubuntu/day-trader-api'
if RUNTIME not in sys.path:
    sys.path.insert(0,RUNTIME)

from live_server import v4_engine as ve
from live_server.db import DB

BASE='http://127.0.0.1:8000'
ET=ZoneInfo('America/New_York')

print('=== V165 DIRECT FROZEN CTX BUILD AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)

# Current runtime symbols from tracker endpoint.
try:
    with urllib.request.urlopen(BASE+'/api/v4/USA/tracker',timeout=5) as r:
        tracker=json.loads(r.read().decode())
except Exception as e:
    print('TRACKER_FETCH_FAIL=',repr(e)); tracker={}
rows=tracker.get('rows') or [] if isinstance(tracker,dict) else []
syms=[]
for r in rows:
    s=str((r or {}).get('symbol') or '').upper()
    if s and s not in syms: syms.append(s)
print('RUNTIME_SYMBOLS=',syms)

# Runtime DB class and live engine singleton setup.
db=DB('/home/ubuntu/day-trader-api/daytrader.db')
eng=ve.V4Engine()

for sym in syms:
    print('\n--- SYMBOL',sym,'---')
    try:
        ticks=db.ticks(sym,40000)
    except Exception as e:
        print('TICKS_ERROR=',repr(e)); continue
    print('TICKS_LEN=',len(ticks or []))
    try:
        b1=ve.ticks_to_bars(ticks,1)
    except Exception as e:
        print('B1_ERROR=',repr(e)); continue
    print('B1_LEN=',0 if b1 is None else len(b1))
    if b1 is None or len(b1)==0:
        print('CTX_DIRECT=None REASON=NO_BARS')
        continue
    print('B1_COLS=',list(b1.columns))
    # Normalize timestamps to ET for diagnostic session partitioning only.
    try:
        ts=pd.to_datetime(b1['time'],utc=True,errors='coerce')
        et=ts.dt.tz_convert(ET)
    except Exception as e:
        print('TIME_PARSE_ERROR=',repr(e)); continue
    x=b1.copy()
    x['_et']=et
    x['_date']=x['_et'].dt.date
    x['_hm']=x['_et'].dt.hour*60+x['_et'].dt.minute
    regular=x[(x['_hm']>=570)&(x['_hm']<960)].copy()  # 09:30-16:00 ET
    dates=sorted([d for d in regular['_date'].dropna().unique()])
    print('REGULAR_DATES_TAIL=',dates[-5:])
    if dates:
        cur_date=dates[-1]
        cur=regular[regular['_date']==cur_date]
        prev_dates=[d for d in dates if d<cur_date]
        prev_date=prev_dates[-1] if prev_dates else None
        prev=regular[regular['_date']==prev_date] if prev_date else regular.iloc[0:0]
        day_open=float(cur.iloc[0]['open']) if len(cur) else None
        prev_high=float(pd.to_numeric(prev['high'],errors='coerce').max()) if len(prev) else None
        prev_low=float(pd.to_numeric(prev['low'],errors='coerce').min()) if len(prev) else None
        print('CUR_REGULAR_DATE=',cur_date,'CUR_BARS=',len(cur),'DAY_OPEN=',day_open)
        print('PREV_REGULAR_DATE=',prev_date,'PREV_BARS=',len(prev),'PREV_HIGH=',prev_high,'PREV_LOW=',prev_low)
    else:
        cur_date=prev_date=None; day_open=prev_high=prev_low=None
        print('NO_REGULAR_BARS_FOUND=True')

    # Start from actual tracker row if available and inject only the historical inputs
    # V161 is supposed to make available causally.
    actual=next((dict(r) for r in rows if str((r or {}).get('symbol') or '').upper()==sym),{})
    actual['market']='USA'
    if day_open: actual['day_open']=day_open
    if prev_high: actual['prev_day_high']=prev_high
    if prev_low: actual['prev_day_low']=prev_low
    try:
        ctx=eng._v142_build_usa_frozen_ctx(actual,b1)
        print('CTX_DIRECT_TYPE=',type(ctx).__name__)
        print('CTX_DIRECT_READY=',bool(isinstance(ctx,dict) and ctx.get('entry_args')))
        if isinstance(ctx,dict):
            print('CTX_KEYS=',list(ctx.keys()))
            ea=ctx.get('entry_args') or {}
            print('ENTRY_ARGS_SUMMARY=',{k:ea.get(k) for k in ('ts','prev_crossed','cross_now','rsi2','day_open','prev_high','prev_low','volume','prior10_volume_avg','cci20','macd_hist','prev_macd_hist')})
            print('EXIT_ARGS_PRESENT=',bool(ctx.get('exit_args')))
        else:
            print('CTX_DIRECT=',ctx)
    except Exception as e:
        print('CTX_DIRECT_ERROR=',type(e).__name__,str(e))

print('\nNEXT=IF_SPCX_CTX_DIRECT_READY_TRUE_FIX_ROW_WIRING_TIMING; ELSE_FIX_SESSION_INPUT_EXTRACTION_ONLY. LENZ_TICKS0_IS_SEPARATE_FEED_GAP')
