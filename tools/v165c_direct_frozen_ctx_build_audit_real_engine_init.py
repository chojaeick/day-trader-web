#!/usr/bin/env python3
"""V165C direct frozen ctx audit using real CleanEngine(db_path).
READ ONLY. No strategy/order/service mutation.
Run with runtime venv python.
"""
from pathlib import Path
import sys, json, urllib.request

RUNTIME=Path('/home/ubuntu/day-trader-api')
DB_PATH=str(RUNTIME/'daytrader.db')
if str(RUNTIME) not in sys.path:
    sys.path.insert(0,str(RUNTIME))

from live_server import v4_engine as ve

print('=== V165C DIRECT FROZEN CTX BUILD AUDIT (REAL ENGINE INIT) ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PYTHON=',sys.executable)
print('DB_PATH=',DB_PATH)
print('ENGINE_CLASS=',getattr(ve,'CleanEngine',None))

Engine=getattr(ve,'CleanEngine',None)
if Engine is None:
    raise SystemExit('CleanEngine_NOT_FOUND')
eng=Engine(DB_PATH)
print('ENGINE_INIT=PASS')
print('ENGINE_DB_TYPE=',type(getattr(eng,'db',None)).__name__)

# collect current runtime tracker rows
rows=[]
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/tracker',timeout=5) as r:
        d=json.loads(r.read().decode('utf-8','ignore'))
        rows=(d.get('rows') or []) if isinstance(d,dict) else []
except Exception as e:
    print('TRACKER_FETCH_ERR=',repr(e))
print('RUNTIME_SYMBOLS=',[r.get('symbol') for r in rows if isinstance(r,dict)])
rowmap={str(r.get('symbol') or '').upper():r for r in rows if isinstance(r,dict)}

for sym in ['SPCX','LENZ','SOXS']:
    try:
        ticks=eng.db.ticks(sym,40000)
    except Exception as e:
        print('SYMBOL',sym,'DB_TICKS_ERR=',repr(e)); continue
    b1=ve.ticks_to_bars(ticks,1)
    print('SYMBOL',sym,'TICKS=',len(ticks or []),'B1_LEN=',len(b1) if b1 is not None else None)
    if b1 is None or len(b1)<25:
        print('SYMBOL',sym,'CTX_READY=False REASON=INSUFFICIENT_B1')
        continue
    row=dict(rowmap.get(sym) or {'market':'USA','symbol':sym})
    # Reproduce V161 causal regular-session partition from live b1 to provide row OHLC inputs.
    try:
        t=__import__('pandas').to_datetime(b1['time'],utc=True,errors='coerce').dt.tz_convert('America/New_York')
        tmp=b1.copy(); tmp['_et']=t; tmp['_date']=t.dt.date; tmp['_hm']=t.dt.hour*60+t.dt.minute
        reg=tmp[(tmp['_hm']>=570)&(tmp['_hm']<=959)&tmp['_et'].notna()].copy()
        dates=sorted([x for x in reg['_date'].dropna().unique()])
        print('SYMBOL',sym,'REG_DATES_TAIL=',dates[-3:] if dates else [])
        if len(dates)>=2:
            cur_date=dates[-1]; prev_date=dates[-2]
            cur=reg[reg['_date']==cur_date]
            prev=reg[reg['_date']==prev_date]
            row['day_open']=float(cur.iloc[0]['open']) if len(cur) else None
            row['prev_day_high']=float(__import__('pandas').to_numeric(prev['high'],errors='coerce').max()) if len(prev) else None
            row['prev_day_low']=float(__import__('pandas').to_numeric(prev['low'],errors='coerce').min()) if len(prev) else None
            print('SYMBOL',sym,'DAY_OPEN=',row.get('day_open'),'PREV_HIGH=',row.get('prev_day_high'),'PREV_LOW=',row.get('prev_day_low'))
        else:
            print('SYMBOL',sym,'CTX_READY=False REASON=REGULAR_DATES_LT2')
            continue
    except Exception as e:
        print('SYMBOL',sym,'SESSION_PARTITION_ERR=',repr(e)); continue
    try:
        ctx=eng._v142_build_usa_frozen_ctx(row,b1)
        print('SYMBOL',sym,'CTX_READY=',bool(ctx),'CTX_KEYS=',sorted(ctx.keys()) if isinstance(ctx,dict) else None)
        if isinstance(ctx,dict):
            ea=ctx.get('entry_args')
            xa=ctx.get('exit_args')
            print('SYMBOL',sym,'ENTRY_CTX=',bool(ea),'EXIT_CTX=',bool(xa),'FEATURE_SNAPSHOT=',ctx.get('feature_snapshot'))
    except Exception as e:
        print('SYMBOL',sym,'CTX_BUILD_ERR=',repr(e))

print('NEXT=IF_SPCX_CTX_READY_TRUE_FIX_LIVE_ROW_WIRING_TIMING; ELSE_FIX_SESSION_OHLC_OR_BUILDER_INPUT_ONLY')
