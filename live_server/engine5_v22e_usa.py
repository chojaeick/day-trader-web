from __future__ import annotations

"""Engine5 V22E — isolated USA paper execution adapter.

Purpose:
- DBB/Engine5 is the sole strategy authority for this USA paper ledger.
- Uses completed 5-minute bars only (causal/fail-closed).
- Keeps its own SQLite account/positions/trades, separate from legacy Williams paper tables.
- Supports V22-style stop, +2R half, outer-upper half, momentum-fade/final exits.
- This is an internal paper broker. It never sends a real brokerage order.
"""

import math, os, sqlite3, threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from .double_bollinger_engine5 import DoubleBollingerEngine5
from .analytics import ticks_to_bars

ENGINE_NAME='ENGINE5_V22E_USA_PAPER'
MARKET='USA'
ENTRY_SCORE=50.0
MAX_POSITIONS=4
FEE_BPS=float(os.getenv('V22E_USA_PAPER_FEE_BPS','5') or 5)
SLIPPAGE_BPS=float(os.getenv('V22E_USA_PAPER_SLIPPAGE_BPS','5') or 5)
INITIAL_CASH=float(os.getenv('V22E_USA_PAPER_INITIAL_CASH','100000') or 100000)


def _now(): return datetime.now(timezone.utc).isoformat()
def _f(v,d=0.0):
    try:
        x=float(v); return d if math.isnan(x) or math.isinf(x) else x
    except Exception:return d

def _i(v,d=0):
    try:return int(float(v))
    except Exception:return d


def _completed_5m(db, symbol:str) -> pd.DataFrame:
    ticks=db.ticks(symbol,4000)
    b=ticks_to_bars(ticks,5)
    if b is None or len(b)<2:return pd.DataFrame()
    # Always discard the newest 5m bucket: it may still be forming.
    return b.iloc[:-1].copy().reset_index(drop=True)


def evaluate_entry(b5:pd.DataFrame)->Dict[str,Any]:
    if b5 is None or len(b5)<30:
        return {'enter':False,'reason':'INSUFFICIENT_5M','engine':ENGINE_NAME,'bars':0 if b5 is None else len(b5)}
    e=DoubleBollingerEngine5().with_entry_score(ENTRY_SCORE)
    z=e.enrich(b5)
    if z.empty:return {'enter':False,'reason':'NO_ENGINE_ROWS','engine':ENGINE_NAME}
    r=z.iloc[-1]
    score=_f(r.get('entry_score'))
    enter=bool(r.get('entry_signal'))
    px=_f(r.get('close'))
    iu=_f(r.get('inner_upper')); il=_f(r.get('inner_lower')); ou=_f(r.get('outer_upper'))
    band_r=max(px-il,0.0) if px and il else 0.0
    return {
        'enter':bool(enter and band_r>0),'reason':'V22E_ENTRY' if enter and band_r>0 else 'NO_ENTRY',
        'engine':ENGINE_NAME,'score':score,'effective_score':score,'price':px,
        'bar_time':str(r.get('time') or ''),'inner_upper':iu,'inner_lower':il,'outer_upper':ou,
        'band_r':band_r,'stop_price':px-band_r if band_r else 0.0,'tp1_price':px+2*band_r if band_r else 0.0,
        'entry_gate':bool(r.get('entry_gate')),
    }


def evaluate_exit(b5:pd.DataFrame,pos:Dict[str,Any])->Dict[str,Any]:
    qty=max(0,_i(pos.get('qty')))
    if qty<=0:return {'exit':False,'reason':'NO_POSITION','engine':ENGINE_NAME}
    if b5 is None or len(b5)<30:return {'exit':False,'reason':'INSUFFICIENT_5M','engine':ENGINE_NAME}
    e=DoubleBollingerEngine5().with_entry_score(ENTRY_SCORE)
    z=e.enrich(b5)
    r=z.iloc[-1]
    px=_f(r.get('close')); stop=_f(pos.get('stop_price')); tp1=_f(pos.get('tp1_price'))
    tp1_done=bool(_i(pos.get('tp1_done'))); outer_done=bool(_i(pos.get('outer_reduced')))
    ou=_f(r.get('outer_upper')); il=_f(r.get('inner_lower'))
    if stop and px<=stop:
        return {'exit':True,'sell_qty':qty,'reason':'V22E_STOP_-1R','price':px,'engine':ENGINE_NAME}
    if (not tp1_done) and tp1 and px>=tp1:
        return {'exit':True,'sell_qty':max(1,qty//2),'reason':'V22E_TP1_+2R_50PCT','price':px,'tp1_done':True,'engine':ENGINE_NAME}
    if tp1_done and (not outer_done) and ou and _f(r.get('high'))>=ou:
        return {'exit':True,'sell_qty':max(1,qty//2),'reason':'V22E_RUNNER_OUTER_UPPER_HALF','price':px,'outer_reduced':True,'engine':ENGINE_NAME}
    fade=sum(1 for x in (_f(r.get('mid_slope8'))<=0,_f(r.get('macd_slope_spread'))<=0,_f(r.get('rsi_slope'))<=0) if x)
    if tp1_done and fade>=2:
        return {'exit':True,'sell_qty':qty,'reason':'V22E_RUNNER_MOMENTUM_FADE_2OF3','price':px,'engine':ENGINE_NAME}
    if tp1_done and il and px<il:
        return {'exit':True,'sell_qty':qty,'reason':'V22E_RUNNER_INNER_LOWER_CLOSE','price':px,'engine':ENGINE_NAME}
    return {'exit':False,'reason':'HOLD','price':px,'engine':ENGINE_NAME,'fade_count':fade}


class V22EUsaPaperBroker:
    def __init__(self,db_path:str):
        self.db_path=str(Path(db_path)); self._lock=threading.RLock(); self._init()
    def _c(self):
        c=sqlite3.connect(self.db_path,timeout=30); c.row_factory=sqlite3.Row; return c
    def _init(self):
        with self._c() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS v22e_usa_account(id INTEGER PRIMARY KEY CHECK(id=1),initial_cash REAL,cash REAL,realized_pnl REAL DEFAULT 0,fees REAL DEFAULT 0,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS v22e_usa_positions(symbol TEXT PRIMARY KEY,qty INTEGER,original_qty INTEGER,entry_price REAL,entry_fill REAL,entry_time TEXT,last_price REAL,stop_price REAL,tp1_price REAL,tp1_done INTEGER DEFAULT 0,outer_reduced INTEGER DEFAULT 0,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS v22e_usa_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT,symbol TEXT,side TEXT,qty INTEGER,signal_price REAL,fill_price REAL,fee REAL,realized_pnl REAL,reason TEXT,engine TEXT);
            ''')
            c.execute('INSERT OR IGNORE INTO v22e_usa_account(id,initial_cash,cash,realized_pnl,fees,updated_at) VALUES(1,?,?,0,0,?)',(INITIAL_CASH,INITIAL_CASH,_now()))
    def account(self):
        with self._lock,self._c() as c:
            a=dict(c.execute('SELECT * FROM v22e_usa_account WHERE id=1').fetchone())
            p=[dict(r) for r in c.execute('SELECT * FROM v22e_usa_positions ORDER BY entry_time').fetchall()]
            mv=sum(_f(x['last_price'])*_i(x['qty']) for x in p); eq=_f(a['cash'])+mv
            return {'engine':ENGINE_NAME,'market':'USA','currency':'USD','initial_cash':_f(a['initial_cash']),'cash':_f(a['cash']),'market_value':mv,'equity':eq,'return_pct':(eq/_f(a['initial_cash'])-1)*100 if _f(a['initial_cash']) else 0,'realized_pnl':_f(a['realized_pnl']),'fees':_f(a['fees']),'positions':p,'position_count':len(p),'updated_at':a['updated_at']}
    def position(self,symbol):
        with self._c() as c:
            r=c.execute('SELECT * FROM v22e_usa_positions WHERE symbol=?',(symbol.upper(),)).fetchone(); return dict(r) if r else None
    def mark(self,symbol,price):
        with self._lock,self._c() as c:c.execute('UPDATE v22e_usa_positions SET last_price=?,updated_at=? WHERE symbol=?',(float(price),_now(),symbol.upper()))
    def enter(self,symbol,signal_price,decision):
        symbol=symbol.upper(); signal_price=float(signal_price)
        with self._lock,self._c() as c:
            if c.execute('SELECT 1 FROM v22e_usa_positions WHERE symbol=?',(symbol,)).fetchone():return {'ok':False,'reason':'ALREADY_OPEN'}
            n=c.execute('SELECT COUNT(*) n FROM v22e_usa_positions').fetchone()['n']
            if n>=MAX_POSITIONS:return {'ok':False,'reason':'MAX_POSITIONS'}
            a=c.execute('SELECT * FROM v22e_usa_account WHERE id=1').fetchone(); cash=_f(a['cash']); budget=cash*0.995
            slip=SLIPPAGE_BPS/10000.; fee_rate=FEE_BPS/10000.; fill=signal_price*(1+slip)
            qty=int(budget//(fill*(1+fee_rate)))
            if qty<1:return {'ok':False,'reason':'NO_CASH'}
            gross=fill*qty; fee=gross*fee_rate; debit=gross+fee; ts=_now()
            c.execute('UPDATE v22e_usa_account SET cash=cash-?,fees=fees+?,updated_at=? WHERE id=1',(debit,fee,ts))
            c.execute('INSERT INTO v22e_usa_positions(symbol,qty,original_qty,entry_price,entry_fill,entry_time,last_price,stop_price,tp1_price,tp1_done,outer_reduced,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,0,?)',(symbol,qty,qty,signal_price,fill,ts,signal_price,_f(decision.get('stop_price')),_f(decision.get('tp1_price')),ts))
            c.execute('INSERT INTO v22e_usa_trades(ts,symbol,side,qty,signal_price,fill_price,fee,realized_pnl,reason,engine) VALUES(?,?,?,?,?,?,?,?,?,?)',(ts,symbol,'BUY',qty,signal_price,fill,fee,None,'V22E_ENTRY',ENGINE_NAME))
            return {'ok':True,'side':'BUY','symbol':symbol,'qty':qty,'fill_price':fill,'fee':fee,'engine':ENGINE_NAME}
    def reduce(self,symbol,signal_price,qty,reason,tp1_done=False,outer_reduced=False):
        symbol=symbol.upper(); signal_price=float(signal_price)
        with self._lock,self._c() as c:
            p=c.execute('SELECT * FROM v22e_usa_positions WHERE symbol=?',(symbol,)).fetchone()
            if not p:return {'ok':False,'reason':'NO_POSITION'}
            sell=min(_i(qty),_i(p['qty']))
            if sell<=0:return {'ok':False,'reason':'ZERO_QTY'}
            slip=SLIPPAGE_BPS/10000.; fee_rate=FEE_BPS/10000.; fill=signal_price*(1-slip); gross=fill*sell; fee=gross*fee_rate
            realized=(fill-_f(p['entry_fill']))*sell-fee; remain=_i(p['qty'])-sell; ts=_now()
            c.execute('UPDATE v22e_usa_account SET cash=cash+?,realized_pnl=realized_pnl+?,fees=fees+?,updated_at=? WHERE id=1',(gross-fee,realized,fee,ts))
            if remain<=0:c.execute('DELETE FROM v22e_usa_positions WHERE symbol=?',(symbol,))
            else:c.execute('UPDATE v22e_usa_positions SET qty=?,last_price=?,tp1_done=MAX(tp1_done,?),outer_reduced=MAX(outer_reduced,?),updated_at=? WHERE symbol=?',(remain,signal_price,int(bool(tp1_done)),int(bool(outer_reduced)),ts,symbol))
            c.execute('INSERT INTO v22e_usa_trades(ts,symbol,side,qty,signal_price,fill_price,fee,realized_pnl,reason,engine) VALUES(?,?,?,?,?,?,?,?,?,?)',(ts,symbol,'SELL',sell,signal_price,fill,fee,realized,reason,ENGINE_NAME))
            return {'ok':True,'side':'SELL','symbol':symbol,'qty':sell,'remain':remain,'fill_price':fill,'realized_pnl':realized,'reason':reason,'engine':ENGINE_NAME}


class V22EUsaExecutor:
    def __init__(self,db_path:str):
        self.broker=V22EUsaPaperBroker(db_path); self._attempt={}
    def step(self,v4,db)->Dict[str,Any]:
        f=((getattr(v4,'finder',{}) or {}).get('USA') or {})
        rows=(f.get('rows') or [])[:20]
        # Existing open positions are always managed even if they leave Finder.
        acct=self.broker.account(); syms=[]
        for p in acct['positions']:
            s=str(p.get('symbol') or '').upper()
            if s and s not in syms:syms.append(s)
        for r in rows:
            s=str(r.get('symbol') or '').upper()
            if s and s not in syms:syms.append(s)
        actions=[]
        for sym in syms:
            b5=_completed_5m(db,sym)
            if len(b5)<30:continue
            px=_f(b5.iloc[-1].get('close'))
            pos=self.broker.position(sym)
            if pos:
                self.broker.mark(sym,px); d=evaluate_exit(b5,pos)
                if d.get('exit'):
                    key=('SELL',sym,str(b5.iloc[-1].get('time')),d.get('reason'))
                    if key not in self._attempt:
                        self._attempt[key]=1; actions.append(self.broker.reduce(sym,px,d.get('sell_qty'),d.get('reason'),d.get('tp1_done'),d.get('outer_reduced')))
            else:
                d=evaluate_entry(b5)
                if d.get('enter'):
                    key=('BUY',sym,str(d.get('bar_time')))
                    if key not in self._attempt:
                        self._attempt[key]=1; actions.append(self.broker.enter(sym,px,d))
        return {'engine':ENGINE_NAME,'finder_count':len(rows),'actions':actions,'account':self.broker.account()}
