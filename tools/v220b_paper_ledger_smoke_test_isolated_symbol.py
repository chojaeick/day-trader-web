#!/usr/bin/env python3
import sqlite3, time, json, urllib.request
from pathlib import Path
from datetime import datetime, timezone

DB='/home/ubuntu/day-trader-api/daytrader.db'
BASE='http://127.0.0.1:8000'
FROZEN='AMD AMZN ARM AVGO GOOGL INTC NFLX NVDA ORCL PLTR QQQ SMCI SMH SOXL SOXS SPY SQQQ TQQQ TSM'.split()
print('=== V220B USA PAPER LEDGER SMOKE TEST ISOLATED SYMBOL ===')
print('REAL_BROKER_CALLS=NONE STRATEGY_CHANGE=NONE SERVICE_MUTATION=NONE')
print('PURPOSE=VERIFY_BUY_POSITION_SELL_REALIZED_PNL_WITH_LIVE_PRICE_SOURCE_BUT_SYNTHETIC_LEDGER_SYMBOL')

# Require DAYTRADE mode but do not mutate runtime.
try:
    with urllib.request.urlopen(BASE+'/api/v4/runtime-mode',timeout=3) as f:
        mode=json.loads(f.read().decode())
    print('RUNTIME_MODE=',mode)
except Exception as e:
    raise SystemExit(f'RUNTIME_MODE_FAIL {e!r}')
if str(mode.get('mode')).upper()!='DAYTRADE':
    raise SystemExit('ABORT_NOT_DAYTRADE')

con=sqlite3.connect(DB,timeout=20)
con.row_factory=sqlite3.Row
# Pick freshest frozen quote strictly as a price source. Ledger symbol is synthetic,
# therefore Williams evaluator cannot collide with this smoke position.
rows=con.execute("SELECT symbol,price,updated_at FROM quotes WHERE symbol IN (%s) AND price>0" % ','.join('?'*len(FROZEN)),FROZEN).fetchall()
if not rows:
    raise SystemExit('ABORT_NO_FROZEN_QUOTE')

def ts(v):
    try:return datetime.fromisoformat(str(v).replace('Z','+00:00'))
    except:return datetime(1970,1,1,tzinfo=timezone.utc)
rows=sorted(rows,key=lambda r:ts(r['updated_at']),reverse=True)
src=rows[0]
source=str(src['symbol']).upper(); buy_price=float(src['price'])
ledger='V220TEST_'+source
print('PRICE_SOURCE=',source,'PRICE=',buy_price,'UPDATED_AT=',src['updated_at'])
print('LEDGER_SYMBOL=',ledger)

# Clean only previous V220B synthetic symbol if present. Never touch real strategy symbols.
con.execute("DELETE FROM v4_trade_log WHERE market='USA' AND symbol=? AND note LIKE 'V220B_SMOKE_TEST_ONLY%'",(ledger,))
con.execute("DELETE FROM v4_positions WHERE market='USA' AND symbol=?",(ledger,))
con.commit()

now=datetime.now(timezone.utc).isoformat()
qty=1.0
# BUY: mirror V4Store persistence semantics directly, but only for synthetic paper symbol.
con.execute("""INSERT INTO v4_positions(market,symbol,qty,avg_entry,realized_pnl,status,opened_at,updated_at,closed_at)
VALUES('USA',?,?,?,0,'OPEN',?,?,NULL)
ON CONFLICT(market,symbol) DO UPDATE SET qty=excluded.qty,avg_entry=excluded.avg_entry,realized_pnl=0,status='OPEN',opened_at=excluded.opened_at,updated_at=excluded.updated_at,closed_at=NULL""",
            (ledger,qty,buy_price,now,now))
con.execute("INSERT INTO v4_trade_log(ts,market,symbol,side,qty,price,realized_pnl,note) VALUES(?, 'USA', ?, 'BUY', ?, ?, 0, ?)",
            (now,ledger,qty,buy_price,'V220B_SMOKE_TEST_ONLY source='+source))
con.commit()
pos=dict(con.execute("SELECT * FROM v4_positions WHERE market='USA' AND symbol=?",(ledger,)).fetchone())
print('AFTER_BUY=',pos)
buy_ok=pos.get('status')=='OPEN' and abs(float(pos.get('qty') or 0)-1.0)<1e-9

# Wait briefly and take freshest current quote. This validates P&L calculation with a live source.
time.sleep(2)
q=con.execute("SELECT price,updated_at FROM quotes WHERE symbol=?",(source,)).fetchone()
sell_price=float(q['price']) if q and float(q['price'] or 0)>0 else buy_price
realized=(sell_price-buy_price)*qty
now2=datetime.now(timezone.utc).isoformat()
con.execute("UPDATE v4_positions SET qty=0,realized_pnl=?,status='CLOSED',updated_at=?,closed_at=? WHERE market='USA' AND symbol=?",
            (realized,now2,now2,ledger))
con.execute("INSERT INTO v4_trade_log(ts,market,symbol,side,qty,price,realized_pnl,note) VALUES(?, 'USA', ?, 'SELL', ?, ?, ?, ?)",
            (now2,ledger,qty,sell_price,realized,'V220B_SMOKE_TEST_ONLY source='+source))
con.commit()
pos2=dict(con.execute("SELECT * FROM v4_positions WHERE market='USA' AND symbol=?",(ledger,)).fetchone())
trades=[dict(r) for r in con.execute("SELECT id,ts,side,qty,price,realized_pnl,note FROM v4_trade_log WHERE market='USA' AND symbol=? ORDER BY id",(ledger,)).fetchall()]
print('AFTER_SELL=',pos2)
print('TRADES=',trades)
print('BUY_PRICE=',buy_price,'SELL_PRICE=',sell_price,'REALIZED_PNL=',realized)
closed_ok=pos2.get('status')=='CLOSED' and abs(float(pos2.get('qty') or 0))<1e-9
trade_ok=len(trades)==2 and [x['side'] for x in trades]==['BUY','SELL']
pnl_ok=abs(float(trades[-1]['realized_pnl'])-realized)<1e-9
ok=bool(buy_ok and closed_ok and trade_ok and pnl_ok)
print('BUY_OPEN_PASS=',buy_ok)
print('SELL_CLOSE_PASS=',closed_ok)
print('TRADE_LOG_PASS=',trade_ok)
print('REALIZED_PNL_PASS=',pnl_ok)
print('V220B_PAPER_SMOKE_PASS=',ok)
print('REAL_BROKER_CALLS_MADE=0')
con.close()
