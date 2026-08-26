#!/usr/bin/env python3
from __future__ import annotations
import sqlite3, time, json, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import sys

RUNTIME='/home/ubuntu/day-trader-api'
sys.path.insert(0,RUNTIME)
from live_server.config import Settings
from live_server.db import DB
from live_server.v4_engine import V4Store

print('=== V220 USA PAPER LEDGER SMOKE TEST ===')
print('REAL_BROKER_CALLS=NONE STRATEGY_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PURPOSE=VERIFY_PAPER_BUY_POSITION_SELL_REALIZED_PNL_PIPELINE')

s=Settings(); db=DB(s.db_path); store=V4Store(s.db_path)
BASE='http://127.0.0.1:8000'

def http_json(path,timeout=3):
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as f:
            return f.status,json.loads(f.read().decode())
    except Exception as e:
        return 0,{'error':repr(e)}

code,mode=http_json('/api/v4/runtime-mode',3)
print('RUNTIME_MODE_HTTP=',code,'BODY=',mode)
if code!=200 or str(mode.get('mode')).upper()!='DAYTRADE':
    raise SystemExit('ABORT_NOT_DAYTRADE')

# Do not touch the frozen19 strategy universe or any existing paper position.
frozen=set('AMD AMZN ARM AVGO GOOGL INTC NFLX NVDA ORCL PLTR QQQ SMCI SMH SOXL SOXS SPY SQQQ TQQQ TSM'.split())
open_syms={str(x.get('symbol')).upper() for x in store.positions('USA')}
print('EXISTING_OPEN_POSITIONS=',sorted(open_syms))

# Pick a fresh non-frozen quote already maintained by the running API.
now=datetime.now(timezone.utc)
candidates=[]
for q in db.quotes():
    sym=str(q.get('symbol') or '').upper()
    if not sym or sym in frozen or sym in open_syms: continue
    price=float(q.get('price') or 0)
    if price<=0: continue
    ts=q.get('updated_at')
    age=999999.0
    try:
        age=(now-datetime.fromisoformat(str(ts).replace('Z','+00:00'))).total_seconds()
    except Exception:
        pass
    if age<=300:
        candidates.append((age,sym,price,q))
candidates.sort()
if not candidates:
    raise SystemExit('ABORT_NO_FRESH_NONFROZEN_QUOTE')
age,sym,buy_price,q=candidates[0]
print('TEST_SYMBOL=',sym,'BUY_PRICE=',buy_price,'QUOTE_AGE_SEC=',round(age,1),'EXCHANGE=',q.get('exchange'))

# Extra guard: never run if this symbol already has an OPEN row.
pre=store.position('USA',sym)
print('PRE_POSITION=',pre)
if pre and str(pre.get('status')).upper()=='OPEN' and float(pre.get('qty') or 0)>0:
    raise SystemExit('ABORT_POSITION_ALREADY_OPEN')

tag='V220_SMOKE_TEST_ONLY'
buy_done=False
try:
    p1=store.buy('USA',sym,1,buy_price,note=tag+'_BUY')
    buy_done=True
    print('BUY_RESULT=',p1)
    if not p1 or str(p1.get('status')).upper()!='OPEN' or abs(float(p1.get('qty') or 0)-1.0)>1e-9:
        raise RuntimeError('BUY_POSITION_VERIFY_FAIL')

    time.sleep(2)
    q2=db.quote(sym) or {}
    sell_price=float(q2.get('price') or buy_price)
    if sell_price<=0: sell_price=buy_price
    print('SELL_PRICE=',sell_price,'QUOTE_UPDATED_AT=',q2.get('updated_at'))
    sell_ret=store.sell('USA',sym,1,sell_price,note=tag+'_SELL')
    print('SELL_RESULT=',sell_ret)
    post=store.position('USA',sym)
    print('POST_POSITION=',post)

    con=sqlite3.connect(s.db_path,timeout=20); con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute("SELECT id,ts,market,symbol,side,qty,price,realized_pnl,note FROM v4_trade_log WHERE note LIKE ? ORDER BY id DESC LIMIT 4",(tag+'%',)).fetchall()]
    con.close()
    rows=list(reversed(rows))
    print('TEST_TRADE_LOG=',rows)
    buys=[r for r in rows if r.get('side')=='BUY' and r.get('symbol')==sym]
    sells=[r for r in rows if r.get('side')=='SELL' and r.get('symbol')==sym]
    realized=float(sells[-1].get('realized_pnl') or 0) if sells else None
    pnl_expected=round(sell_price-buy_price,10)
    pnl_ok=(realized is not None and abs(realized-pnl_expected)<1e-6)
    closed_ok=bool(post and (str(post.get('status')).upper()=='CLOSED' or float(post.get('qty') or 0)<=1e-9))
    passed=bool(len(buys)>=1 and len(sells)>=1 and closed_ok and pnl_ok)
    print('EXPECTED_REALIZED_PNL=',pnl_expected,'ACTUAL_REALIZED_PNL=',realized,'PNL_OK=',pnl_ok)
    print('CLOSED_OK=',closed_ok)
    print('V220_PAPER_SMOKE_PASS=',passed)
    print('REAL_BROKER_CALLS_MADE=0')
    print('NOTE=V220 rows remain in v4_trade_log with TEST tag; closed position is non-actionable.')
    if not passed: raise SystemExit(2)
except Exception as e:
    print('SMOKE_ERROR=',repr(e))
    # Emergency cleanup only inside paper ledger; never broker.
    if buy_done:
        try:
            cur=store.position('USA',sym)
            if cur and str(cur.get('status')).upper()=='OPEN' and float(cur.get('qty') or 0)>0:
                qty=float(cur.get('qty') or 0)
                store.sell('USA',sym,qty,buy_price,note=tag+'_EMERGENCY_CLOSE')
                print('EMERGENCY_PAPER_CLOSE=PASS QTY=',qty)
        except Exception as ee:
            print('EMERGENCY_PAPER_CLOSE=FAIL',repr(ee))
    raise
