#!/usr/bin/env python3
"""V167 exact API DB -> tick bars -> frozen ctx replay audit.
READ ONLY. No strategy/order/service mutation.
Run with runtime venv python.
"""
from __future__ import annotations
import sys, json, urllib.request
from datetime import timezone
from zoneinfo import ZoneInfo

from live_server.config import Settings
from live_server.db import DB
from live_server.analytics import ticks_to_bars
from live_server.v4_engine import CleanEngine

print('=== V167 EXACT API DB -> FROZEN CTX REPLAY ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)

s=Settings(); db=DB(s.db_path); eng=CleanEngine(s.db_path)
print('DB_PATH=',s.db_path)
print('DB_TYPE=',type(db).__name__)
print('ENGINE_TYPE=',type(eng).__name__)

BASE='http://127.0.0.1:8000'
def get(path):
    try:
        with urllib.request.urlopen(BASE+path,timeout=5) as r:
            return json.loads(r.read().decode('utf-8','ignore'))
    except Exception:
        return {}

runtime=get('/api/v4/USA/status')
rows=[]
if isinstance(runtime,dict):
    tr=runtime.get('tracker') or {}
    if isinstance(tr,dict): rows.extend(tr.get('rows') or [])
syms=[]
for r in rows:
    x=str((r or {}).get('symbol') or '').upper()
    if x and x not in syms: syms.append(x)
for x in ['SPCX','LENZ','SOXS']:
    if x not in syms: syms.append(x)
print('SYMBOLS=',syms)

ET=ZoneInfo('America/New_York')
for sym in syms:
    try: ticks=db.ticks(sym,40000)
    except Exception as e:
        print('SYMBOL',sym,'TICKS_ERR=',repr(e)); continue
    b1=ticks_to_bars(ticks,1)
    print('SYMBOL',sym,'TICKS=',len(ticks or []),'B1_LEN=',len(b1) if b1 is not None else None)
    if b1 is None or len(b1)<25:
        print('SYMBOL',sym,'CTX_READY=',False,'REASON=B1_LT25'); continue
    row=next((r for r in rows if str((r or {}).get('symbol') or '').upper()==sym),None) or {'market':'USA','symbol':sym,'price':float(b1.iloc[-1]['close'])}
    row=dict(row); row['market']='USA'

    # Reproduce V161 session partition directly from b1 timestamps, using ET wall-clock.
    try:
        x=b1.copy()
        import pandas as pd
        ts=pd.to_datetime(x['time'],utc=True,errors='coerce')
        x=x.assign(_et=ts.dt.tz_convert('America/New_York'))
        x=x[x['_et'].notna()].copy()
        x['_date']=x['_et'].dt.date
        x['_minute']=x['_et'].dt.hour*60+x['_et'].dt.minute
        reg=x[(x['_minute']>=570)&(x['_minute']<=960)]
        dates=sorted(reg['_date'].unique()) if not reg.empty else []
        latest_date=dates[-1] if dates else None
        prev_date=dates[-2] if len(dates)>=2 else None
        if latest_date is not None:
            cur=reg[reg['_date']==latest_date]
            if not cur.empty: row['day_open']=float(cur.iloc[0]['open'])
        if prev_date is not None:
            prev=reg[reg['_date']==prev_date]
            if not prev.empty:
                row['prev_day_high']=float(prev['high'].max()); row['prev_day_low']=float(prev['low'].min())
        print('SYMBOL',sym,'LATEST_REG_DATE=',latest_date,'PREV_REG_DATE=',prev_date,'DAY_OPEN=',row.get('day_open'),'PREV_HIGH=',row.get('prev_day_high'),'PREV_LOW=',row.get('prev_day_low'))
    except Exception as e:
        print('SYMBOL',sym,'SESSION_PARTITION_ERR=',repr(e))

    try:
        ctx=eng._v142_build_usa_frozen_ctx(row,b1)
        print('SYMBOL',sym,'CTX_READY=',bool(ctx),'CTX_KEYS=',sorted(ctx.keys()) if isinstance(ctx,dict) else None)
        if isinstance(ctx,dict):
            print('SYMBOL',sym,'ENTRY_CTX=',ctx.get('entry_args'))
            print('SYMBOL',sym,'EXIT_CTX=',ctx.get('exit_args'))
    except Exception as e:
        print('SYMBOL',sym,'CTX_ERR=',repr(e))

print('NEXT=IF_SPCX_CTX_READY_TRUE_PATCH_ONLY_LIVE_ROW_WIRING_TIMING; ELSE_FIX_SESSION_OHLC_INPUT_ONLY')
