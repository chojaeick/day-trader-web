#!/usr/bin/env python3
"""V167B exact API DB -> frozen ctx replay with runtime sys.path fixed.
READ ONLY. No strategy/order/service mutation.
"""
from __future__ import annotations
import sys, json, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
if str(RUNTIME) not in sys.path:
    sys.path.insert(0,str(RUNTIME))

print('=== V167B EXACT API DB CTX REPLAY (PATH FIX) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)
print('RUNTIME_PATH_IN_SYSPATH=',str(RUNTIME) in sys.path)

from live_server.config import Settings
from live_server.db import DB
from live_server.analytics import ticks_to_bars
from live_server import v4_engine as ve

s=Settings()
db=DB(s.db_path)
eng=ve.CleanEngine(s.db_path)
print('DB_PATH=',s.db_path)
print('ENGINE_CLASS=',type(eng).__name__)

def get_runtime_symbols():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=5) as r:
            d=json.loads(r.read().decode())
        rows=((d or {}).get('tracker') or {}).get('rows') or []
        return [str(x.get('symbol') or '').upper() for x in rows if x.get('symbol')]
    except Exception as e:
        print('STATUS_ERR=',repr(e)); return []

syms=get_runtime_symbols()
if not syms:
    syms=['SPCX','LENZ','SOXS']
print('RUNTIME_SYMBOLS=',syms)

for sym in syms:
    try:
        ticks=db.ticks(sym,40000)
        b1=ticks_to_bars(ticks,1)
        print('SYMBOL',sym,'TICKS=',len(ticks or []),'B1_LEN=',len(b1) if b1 is not None else None)
        if b1 is None or len(b1)<25:
            print('SYMBOL',sym,'CTX_READY=',False,'REASON=INSUFFICIENT_B1')
            continue

        # Reproduce V161 causal regular-session day partition from existing bars.
        x=b1.copy()
        if 'time' not in x.columns:
            print('SYMBOL',sym,'CTX_READY=',False,'REASON=NO_TIME_COL'); continue
        import pandas as pd
        t=pd.to_datetime(x['time'],utc=True,errors='coerce')
        et=t.dt.tz_convert('America/New_York')
        x=x.assign(_et=et,_date=et.dt.date,_hm=et.dt.hour*60+et.dt.minute)
        reg=x[(x['_hm']>=570)&(x['_hm']<=960)].copy()
        dates=sorted(d for d in reg['_date'].dropna().unique())
        print('SYMBOL',sym,'REG_DATES=',dates[-5:])
        if len(dates)<2:
            print('SYMBOL',sym,'CTX_READY=',False,'REASON=NEED_2_REGULAR_DATES'); continue
        curd=dates[-1]; prevd=dates[-2]
        cur=reg[reg['_date']==curd]
        prev=reg[reg['_date']==prevd]
        if cur.empty or prev.empty:
            print('SYMBOL',sym,'CTX_READY=',False,'REASON=EMPTY_CUR_OR_PREV'); continue
        day_open=float(cur.iloc[0]['open'])
        prev_high=float(prev['high'].max())
        prev_low=float(prev['low'].min())
        row={'market':'USA','symbol':sym,'price':float(b1.iloc[-1]['close']),
             'day_open':day_open,'prev_day_high':prev_high,'prev_day_low':prev_low}
        try:
            pos=eng.paper.position('USA',sym) if hasattr(eng.paper,'position') else None
        except Exception:
            pos=None
        if pos:
            row['avg_entry']=pos.get('avg_entry') or pos.get('price')
        ctx=eng._v142_build_usa_frozen_ctx(row,b1)
        print('SYMBOL',sym,'DAY_OPEN=',day_open,'PREV_HIGH=',prev_high,'PREV_LOW=',prev_low)
        print('SYMBOL',sym,'CTX_READY=',bool(ctx),'CTX_KEYS=',sorted(ctx.keys()) if isinstance(ctx,dict) else None)
        if isinstance(ctx,dict):
            print('SYMBOL',sym,'ENTRY_CTX=',ctx.get('entry_args'))
            print('SYMBOL',sym,'EXIT_CTX=',ctx.get('exit_args'))
    except Exception as e:
        print('SYMBOL',sym,'ERROR=',repr(e))

print('NEXT=IF_SPCX_CTX_READY_TRUE_FIX_LIVE_ROW_WIRING_TIMING; ELSE_FIX_SESSION_OHLC_OR_BUILDER_INPUT_ONLY')
