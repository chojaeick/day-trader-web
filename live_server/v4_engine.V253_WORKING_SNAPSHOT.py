from __future__ import annotations
import json, math, sqlite3, threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from .analytics import ticks_to_bars, multi_timeframe_signal

# V140 frozen USA Williams paper evaluator (V139 replay-equivalent)
try:
    from . import williams_usa_frozen as _wuf
except Exception:
    _wuf=None
from .paper_trading import PaperBroker
from .rebound_engine import evaluate_rebound
from .position_intelligence import build_position_intelligence, load_portfolio_config

SEMI={'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'}
TRACK_LIMIT=5
POWER_ALERT_DELTA=12
RANK_ALERT_DELTA=2
STATE_PRIORITY={'HARD_EXIT':0,'EXIT_READY':1,'PARTIAL_EXIT':2,'ENTRY':3,'READY':4,'SETUP':5,'HOLD':6,'WATCH':7,'DATA_INVALID':99}
def _tracker_sort_key(r):
    return (0 if r.get('position_open') else 1,STATE_PRIORITY.get(str(r.get('state')),99),-abs(_f(r.get('power'))),-abs(_f(r.get('power_delta'))),r.get('finder_rank') or 99)

def _f(v,default=0.0):
    try:
        x=float(v)
        return default if math.isnan(x) or math.isinf(x) else x
    except Exception:return default

def _clip(v,lo,hi): return max(lo,min(hi,v))
def _now(): return datetime.now(timezone.utc).isoformat()
def _session(market):
    market=market.upper(); tz='America/New_York' if market=='USA' else 'Asia/Seoul'
    t=datetime.now(timezone.utc).astimezone(ZoneInfo(tz))
    if t.weekday()>=5:return 'CLOSED'
    m=t.hour*60+t.minute
    if market=='USA':
        if 240<=m<570:return 'PREMARKET'
        if 570<=m<960:return 'REGULAR'
        if 960<=m<1200:return 'AFTER'
        return 'CLOSED'
    if 500<=m<540:return 'PREOPEN'
    if 540<=m<930:return 'REGULAR'
    return 'CLOSED'

def _ts_utc(v):
    if v is None:return None
    try:
        x=pd.to_datetime(v,utc=True); return x.to_pydatetime() if hasattr(x,'to_pydatetime') else x
    except Exception:return None

def _bar_last_ts(df):
    if df is None or len(df)==0:return None
    for col in ('time','ts','timestamp','datetime'):
        if col in df.columns:return _ts_utc(df.iloc[-1][col])
    return None

def _data_integrity_usa(price,b1,b5,session):
    reasons=[]; now=datetime.now(timezone.utc)
    if b1 is None or len(b1)<8:reasons.append('1분봉 부족')
    if b5 is None or len(b5)<4:reasons.append('5분봉 부족')
    t1=_bar_last_ts(b1); t5=_bar_last_ts(b5)
    if session=='REGULAR':
        if t1 is None:reasons.append('1분봉 시간 없음')
        else:
            age1=(now-t1).total_seconds()
            if age1>180:reasons.append(f'1분봉 지연 {int(age1)}초')
            if age1<-120:reasons.append('1분봉 미래시각 오류')
        if t5 is None:reasons.append('5분봉 시간 없음')
        else:
            age5=(now-t5).total_seconds()
            if age5>480:reasons.append(f'5분봉 지연 {int(age5)}초')
            if age5<-120:reasons.append('5분봉 미래시각 오류')
    last1=_f(b1.iloc[-1]['close']) if b1 is not None and len(b1) and 'close' in b1.columns else 0
    last5=_f(b5.iloc[-1]['close']) if b5 is not None and len(b5) and 'close' in b5.columns else 0
    if price and last1:
        gap1=abs(price/last1-1)*100
        if gap1>2.5:reasons.append(f'현재가-1분봉 불일치 {gap1:.1f}%')
    if price and last5:
        gap5=abs(price/last5-1)*100
        if gap5>4.0:reasons.append(f'현재가-5분봉 불일치 {gap5:.1f}%')
    valid=len(reasons)==0
    return {'valid':valid,'actionable':bool(valid and session=='REGULAR'),'reasons':reasons,
            'last_1m_time':t1.isoformat() if t1 else None,'last_5m_time':t5.isoformat() if t5 else None,
            'last_1m_close':last1 or None,'last_5m_close':last5 or None}

def _usa_entry_trigger(price,vwap,ema9,ema20,rsi,over_vwap,vol_ratio,power,delta,b1,b5,risk):

    """V4.7.3 USA long-entry gate.



    5m = directional setup/trend.

    1m = actual breakout/participation trigger.



    READY calibration:

    - Setup >= 3/4

    - Non-Power 1m trigger >= 3/4

    - Power >= 40

    - Chase guard must pass



    ENTRY remains intentionally strict.

    """

    setup_checks={}

    trigger_checks={}



    if len(b5)>=3:

        c0=_f(b5.iloc[-1]['close'])

        c1=_f(b5.iloc[-2]['close'])

        c2=_f(b5.iloc[-3]['close'])

        l0=_f(b5.iloc[-1]['low'])

        l1=_f(b5.iloc[-2]['low'])



        setup_checks={

            'price_above_vwap':bool(price and vwap and price>vwap),

            'ema9_above_ema20':bool(ema9 and ema20 and ema9>ema20),

            'five_min_rising':bool(c0>c1),

            'five_min_structure':bool(

                (c0>c1>c2) or

                (l0>l1 and c0>=c1)

            ),

        }

    else:

        setup_checks={

            'price_above_vwap':False,

            'ema9_above_ema20':False,

            'five_min_rising':False,

            'five_min_structure':False

        }



    if len(b1)>=3:

        last=b1.iloc[-1]

        prev=b1.iloc[-2]



        lc=_f(last['close'])

        lo=_f(last['open'])

        ph=_f(prev['high'])

        pc=_f(prev['close'])



        one_ret=((lc/pc-1)*100) if pc else 0



        trigger_checks={

            'green_1m':bool(lc>lo),

            'break_prev_high':bool(lc>ph),

            'volume_expansion':bool(vol_ratio>=1.5),

            'one_min_impulse':bool(one_ret>=0.15),



            # V4.7.3:

            # retained as diagnostic/ENTRY confirmation,

            # but no longer counted as a READY price-volume trigger.

            'power_acceleration':bool(

                power>=60 and delta>=4

            ),

        }

    else:

        one_ret=0.0

        trigger_checks={

            'green_1m':False,

            'break_prev_high':False,

            'volume_expansion':False,

            'one_min_impulse':False,

            'power_acceleration':False

        }



    setup_count=sum(1 for v in setup_checks.values() if v)

    trigger_count=sum(1 for v in trigger_checks.values() if v)



    nonpower_keys=(

        'green_1m',

        'break_prev_high',

        'volume_expansion',

        'one_min_impulse'

    )



    nonpower_trigger_count=sum(

        1 for k in nonpower_keys

        if trigger_checks.get(k)

    )



    setup_ok=setup_count>=3



    chase_ok=bool(

        risk=='NORMAL'

        and rsi<74

        and over_vwap<2.5

    )



    # V4.7.3 READY:

    # historical validation showed excessive Power gating

    # was suppressing otherwise valid price/volume setups.

    ready=bool(

        setup_ok

        and nonpower_trigger_count>=3

        and power>=40

        and chase_ok

    )



    # ENTRY is NOT loosened in V4.7.3.

    # V4.8.13: price/volume confirmation drives live ENTRY.

    # Power acceleration remains diagnostic only.

    trigger_core=bool(

        trigger_checks.get('green_1m')

        and trigger_checks.get('break_prev_high')

        and trigger_checks.get('volume_expansion')

        and trigger_checks.get('one_min_impulse')

    )



    entry=bool(

        setup_count>=4

        and nonpower_trigger_count>=4

        and trigger_core

        and power>=50

        and chase_ok

    )





    # V4.7.4 signal grading

    if entry:

        signal_grade='ENTRY'

    elif ready and setup_count>=4 and nonpower_trigger_count>=4:

        signal_grade='ENTRY_CANDIDATE'

    elif ready and setup_count>=4 and nonpower_trigger_count>=3:

        signal_grade='READY_STRONG'

    elif ready:

        signal_grade='READY_WATCH'

    elif setup_ok:

        signal_grade='SETUP'

    else:

        signal_grade='WATCH'



    return {

        'setup_ok':setup_ok,

        'ready':ready,

        'entry':entry,

        'signal_grade':signal_grade,

        'grade_rule':'V4.7.4 WATCH=current READY · STRONG=S4/N3 · ENTRY_CANDIDATE=S4/N4 · ENTRY=strict',



        'setup_count':setup_count,

        'setup_total':len(setup_checks),



        # Backward-compatible total still contains Power acceleration.

        'trigger_count':trigger_count,

        'trigger_total':len(trigger_checks),



        # New V4.7.3 READY diagnostics.

        'nonpower_trigger_count':nonpower_trigger_count,

        'nonpower_trigger_total':4,

        'ready_power_min':40,

        'entry_power_min':50,



        'setup_checks':setup_checks,

        'trigger_checks':trigger_checks,

        'one_min_return_pct':round(one_ret,3),

        'chase_ok':chase_ok,



        'rule':(

            'V4.7.3 · READY=5m Setup>=3/4 + '

            'non-Power 1m Trigger>=3/4 + Power>=40 + chase guard · '

            'ENTRY remains strict'

        ),

    }




class V4Store:
    def __init__(self,path): self.path=path; self._init()
    def _c(self):
        c=sqlite3.connect(self.path,timeout=20); c.row_factory=sqlite3.Row; return c
    def _init(self):
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS v4_positions(
                market TEXT NOT NULL,symbol TEXT NOT NULL,qty REAL NOT NULL DEFAULT 0,
                avg_entry REAL NOT NULL DEFAULT 0,realized_pnl REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'OPEN',opened_at TEXT,updated_at TEXT,closed_at TEXT,
                initial_floor REAL,current_floor REAL,warning_floor REAL,high_watermark REAL,
                floor_mode TEXT,entry_power REAL,partial_exit_done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(market,symbol)
            );
            CREATE TABLE IF NOT EXISTS v4_trade_log(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,qty REAL NOT NULL,price REAL NOT NULL,realized_pnl REAL NOT NULL DEFAULT 0,note TEXT);
            CREATE TABLE IF NOT EXISTS v4_signal_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT,event_type TEXT NOT NULL,state_from TEXT,state_to TEXT,power REAL,rank_from INTEGER,rank_to INTEGER,message TEXT,payload_json TEXT);
            CREATE TABLE IF NOT EXISTS v4_tracker_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT NOT NULL,finder_rank INTEGER,power REAL,power_delta REAL,state TEXT,risk TEXT,price REAL,payload_json TEXT);
            CREATE TABLE IF NOT EXISTS v4_validation_marks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT NOT NULL,state TEXT,
                anchor_price REAL,power REAL,power_delta REAL,finder_rank INTEGER,
                setup_count INTEGER,trigger_count INTEGER,rvol REAL,volume_ratio REAL,
                hard_floor REAL,warning_floor REAL,floor_mode TEXT,
                ret_5m REAL,ret_15m REAL,ret_30m REAL,ret_60m REAL,
                mfe_pct REAL NOT NULL DEFAULT 0,mae_pct REAL NOT NULL DEFAULT 0,
                settled_at TEXT,feature_json TEXT,
                UNIQUE(market,symbol,ts)
            );
            """)
            cols={r[1] for r in c.execute("PRAGMA table_info(v4_positions)").fetchall()}
            migrations=[('initial_floor','REAL'),('current_floor','REAL'),('warning_floor','REAL'),('high_watermark','REAL'),('floor_mode','TEXT'),('entry_power','REAL'),('partial_exit_done','INTEGER NOT NULL DEFAULT 0')]
            for name,ctype in migrations:
                if name not in cols:c.execute(f"ALTER TABLE v4_positions ADD COLUMN {name} {ctype}")
            # V4.3.1: remove closed-session validation contamination from V4.3.
            c.execute("DELETE FROM v4_validation_marks WHERE floor_mode='REFERENCE_ONLY'")
    def positions(self,market=None):
        sql="SELECT * FROM v4_positions WHERE status='OPEN'"; args=[]
        if market: sql+=' AND market=?'; args=[market.upper()]
        sql+=' ORDER BY updated_at DESC'
        with self._c() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]
    def position(self,market,symbol):
        with self._c() as c:
            r=c.execute('SELECT * FROM v4_positions WHERE market=? AND symbol=?',(market.upper(),symbol.upper())).fetchone(); return dict(r) if r else None
    def buy(self,market,symbol,qty,price,note=''):
        market=market.upper(); symbol=symbol.upper(); qty=_f(qty); price=_f(price)
        if qty<=0 or price<=0: raise ValueError('qty and price must be > 0')
        with self._c() as c:
            r=c.execute('SELECT * FROM v4_positions WHERE market=? AND symbol=?',(market,symbol)).fetchone(); now=_now()
            if r and r['status']=='OPEN' and _f(r['qty'])>0:
                oq=_f(r['qty']); oa=_f(r['avg_entry']); nq=oq+qty; avg=(oq*oa+qty*price)/nq
                c.execute('UPDATE v4_positions SET qty=?,avg_entry=?,updated_at=? WHERE market=? AND symbol=?',(nq,avg,now,market,symbol))
            else:
                c.execute("""INSERT INTO v4_positions(market,symbol,qty,avg_entry,realized_pnl,status,opened_at,updated_at,closed_at) VALUES(?,?,?,?,0,'OPEN',?,?,NULL) ON CONFLICT(market,symbol) DO UPDATE SET qty=excluded.qty,avg_entry=excluded.avg_entry,realized_pnl=0,status='OPEN',opened_at=excluded.opened_at,updated_at=excluded.updated_at,closed_at=NULL""",(market,symbol,qty,price,now,now))
            c.execute('INSERT INTO v4_trade_log(ts,market,symbol,side,qty,price,realized_pnl,note) VALUES(?,?,?,?,?,?,0,?)',(now,market,symbol,'BUY',qty,price,note))
        return self.position(market,symbol)
    def sell(self,market,symbol,qty,price,note=''):
        market=market.upper(); symbol=symbol.upper(); qty=_f(qty); price=_f(price)
        if qty<=0 or price<=0: raise ValueError('qty and price must be > 0')
        with self._c() as c:
            r=c.execute("SELECT * FROM v4_positions WHERE market=? AND symbol=? AND status='OPEN'",(market,symbol)).fetchone()
            if not r: raise ValueError('open position not found')
            held=_f(r['qty']); avg=_f(r['avg_entry'])
            if qty>held+1e-9: raise ValueError(f'sell qty exceeds held qty ({held})')
            realized=(price-avg)*qty; remain=max(0.0,held-qty); total=_f(r['realized_pnl'])+realized; now=_now()
            if remain<=1e-9:c.execute("UPDATE v4_positions SET qty=0,realized_pnl=?,status='CLOSED',updated_at=?,closed_at=? WHERE market=? AND symbol=?",(total,now,now,market,symbol))
            else:c.execute('UPDATE v4_positions SET qty=?,realized_pnl=?,updated_at=? WHERE market=? AND symbol=?',(remain,total,now,market,symbol))
            c.execute('INSERT INTO v4_trade_log(ts,market,symbol,side,qty,price,realized_pnl,note) VALUES(?,?,?,?,?,?,?,?)',(now,market,symbol,'SELL',qty,price,realized,note))
        return {'remaining_qty':remain,'realized_this_trade':realized,'realized_total':total}
    def trades(self,market=None,limit=200):
        sql='SELECT * FROM v4_trade_log'; args=[]
        if market:sql+=' WHERE market=?'; args=[market.upper()]
        sql+=' ORDER BY id DESC LIMIT ?'; args.append(int(limit))
        with self._c() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]
    def event(self,market,symbol,event_type,state_from=None,state_to=None,power=None,rank_from=None,rank_to=None,message='',payload=None):
        with self._c() as c:c.execute('INSERT INTO v4_signal_events(ts,market,symbol,event_type,state_from,state_to,power,rank_from,rank_to,message,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(_now(),market.upper(),symbol,event_type,state_from,state_to,power,rank_from,rank_to,message,json.dumps(payload or {},ensure_ascii=False,default=str)))
    def events(self,market=None,limit=50):
        sql='SELECT * FROM v4_signal_events'; args=[]
        if market:sql+=' WHERE market=?'; args=[market.upper()]
        sql+=' ORDER BY id DESC LIMIT ?'; args.append(int(limit))
        with self._c() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]
    def snapshot(self,row):
        with self._c() as c:c.execute('INSERT INTO v4_tracker_snapshots(ts,market,symbol,finder_rank,power,power_delta,state,risk,price,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(_now(),row.get('market'),row.get('symbol'),row.get('finder_rank'),row.get('power'),row.get('power_delta'),row.get('state'),row.get('risk'),row.get('price'),json.dumps(row,ensure_ascii=False,default=str)))
    def snapshots(self,market=None,limit=500):
        sql='SELECT id,ts,market,symbol,finder_rank,power,power_delta,state,risk,price FROM v4_tracker_snapshots'; args=[]
        if market:sql+=' WHERE market=?'; args=[market.upper()]
        sql+=' ORDER BY id DESC LIMIT ?'; args.append(int(limit))
        with self._c() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]

    def update_position_risk(self,market,symbol,initial_floor,current_floor,warning_floor,high_watermark,floor_mode,entry_power=None,partial_exit_done=None):
        fields=['initial_floor=?','current_floor=?','warning_floor=?','high_watermark=?','floor_mode=?','updated_at=?']; vals=[initial_floor,current_floor,warning_floor,high_watermark,floor_mode,_now()]
        if entry_power is not None: fields.append('entry_power=?'); vals.append(entry_power)
        if partial_exit_done is not None: fields.append('partial_exit_done=?'); vals.append(int(bool(partial_exit_done)))
        vals.extend([market.upper(),symbol.upper()])
        with self._c() as c:c.execute(f"UPDATE v4_positions SET {','.join(fields)} WHERE market=? AND symbol=?",vals)

    def add_validation_mark(self,row):
        if row.get('market')!='USA' or not row.get('price'):return
        if not (row.get('data_integrity') or {}).get('valid'):return
        minute=str(row.get('updated_at') or _now())[:16]+':00+00:00'; gate=row.get('entry_gate') or {}; comp=row.get('components') or {}
        with self._c() as c:
            c.execute("""INSERT OR IGNORE INTO v4_validation_marks(ts,market,symbol,state,anchor_price,power,power_delta,finder_rank,setup_count,trigger_count,rvol,volume_ratio,hard_floor,warning_floor,floor_mode,mfe_pct,mae_pct,feature_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (minute,'USA',row.get('symbol'),row.get('state'),row.get('price'),row.get('power'),row.get('power_delta'),row.get('finder_rank'),gate.get('setup_count'),gate.get('trigger_count'),comp.get('rvol'),comp.get('volume_ratio'),row.get('hard_floor'),row.get('warning_floor'),row.get('floor_mode'),0.0,0.0,json.dumps(row,ensure_ascii=False,default=str)))


    def add_korea_validation_mark(self,row):
        """Store one regular-session KR tracker observation per minute.

        KR does not yet have a verified 1m/5m chart gate, so setup/trigger fields
        are intentionally left NULL. This is observational validation only.
        """
        if str(row.get('market') or '').upper()!='KOREA' or not row.get('price'):
            return
        if str(row.get('session') or '').upper()!='REGULAR':
            return
        minute=str(row.get('updated_at') or _now())[:16]+':00+00:00'
        with self._c() as c:
            c.execute("""INSERT OR IGNORE INTO v4_validation_marks(
                ts,market,symbol,state,anchor_price,power,power_delta,finder_rank,
                setup_count,trigger_count,rvol,volume_ratio,
                hard_floor,warning_floor,floor_mode,mfe_pct,mae_pct,feature_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    minute,'KOREA',row.get('symbol'),row.get('state'),row.get('price'),
                    row.get('power'),row.get('power_delta'),row.get('finder_rank'),
                    None,None,None,None,None,None,'KR_REFERENCE',
                    0.0,0.0,json.dumps(row,ensure_ascii=False,default=str)
                )
            )

    def backfill_korea_validation_from_snapshots(self,session_date=None):
        """Retrofit existing KR tracker snapshots into validation marks."""
        tz=ZoneInfo('Asia/Seoul')
        with self._c() as c:
            rows=[dict(r) for r in c.execute(
                """SELECT id,ts,market,symbol,finder_rank,power,power_delta,state,risk,price,payload_json
                   FROM v4_tracker_snapshots
                   WHERE market='KOREA'
                   ORDER BY symbol,ts"""
            ).fetchall()]

        parsed=[]
        for r in rows:
            try:
                t=datetime.fromisoformat(str(r.get('ts')).replace('Z','+00:00'))
                if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            kst=t.astimezone(tz)
            day=kst.date().isoformat()
            if session_date and day!=session_date:
                continue
            mins=kst.hour*60+kst.minute
            if not (9*60 <= mins < 15*60+30):
                continue
            if _f(r.get('price'))<=0:
                continue
            try:
                payload=json.loads(r.get('payload_json') or '{}')
            except Exception:
                payload={}
            parsed.append((r,t,day,payload))

        by_symbol={}
        for item in parsed:
            by_symbol.setdefault(str(item[0].get('symbol') or '').upper(),[]).append(item)

        inserted=0
        with self._c() as c:
            for sym,items in by_symbol.items():
                items.sort(key=lambda x:x[1])
                for idx,(r,t,day,payload) in enumerate(items):
                    minute=t.replace(second=0,microsecond=0).isoformat()
                    anchor=_f(r.get('price'))
                    if not anchor:
                        continue

                    future=items[idx:]
                    def first_after(target_min):
                        for rr,tt,dd,pp in future:
                            if (tt-t).total_seconds() >= target_min*60:
                                return _f(rr.get('price')) or None
                        return None

                    p5=first_after(5); p15=first_after(15)
                    p30=first_after(30); p60=first_after(60)
                    within60=[
                        _f(rr.get('price')) for rr,tt,dd,pp in future
                        if 0 <= (tt-t).total_seconds() <= 60*60 and _f(rr.get('price'))>0
                    ]
                    rets=[(p/anchor-1)*100 for p in within60] if within60 else [0.0]
                    settled_at=None
                    for rr,tt,dd,pp in future:
                        if (tt-t).total_seconds() >= 60*60:
                            settled_at=tt.isoformat(); break

                    c.execute("""INSERT OR IGNORE INTO v4_validation_marks(
                        ts,market,symbol,state,anchor_price,power,power_delta,finder_rank,
                        setup_count,trigger_count,rvol,volume_ratio,
                        hard_floor,warning_floor,floor_mode,
                        ret_5m,ret_15m,ret_30m,ret_60m,mfe_pct,mae_pct,settled_at,feature_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            minute,'KOREA',sym,r.get('state'),anchor,r.get('power'),
                            r.get('power_delta'),r.get('finder_rank'),
                            None,None,None,None,None,None,'KR_REFERENCE',
                            ((p5/anchor-1)*100 if p5 else None),
                            ((p15/anchor-1)*100 if p15 else None),
                            ((p30/anchor-1)*100 if p30 else None),
                            ((p60/anchor-1)*100 if p60 else None),
                            max(rets),min(rets),settled_at,
                            json.dumps(payload or r,ensure_ascii=False,default=str)
                        )
                    )
                    if c.execute("SELECT changes()").fetchone()[0]:
                        inserted+=1
        return {'inserted':inserted,'source_snapshots':len(parsed),'symbols':len(by_symbol)}

    def korea_session_report(self,session_date=None):
        tz=ZoneInfo('Asia/Seoul')
        if not session_date:
            session_date=datetime.now(timezone.utc).astimezone(tz).date().isoformat()

        rows=self.validation_marks('KOREA',10000)
        day=[]
        for r in rows:
            try:
                t=datetime.fromisoformat(str(r.get('ts')).replace('Z','+00:00'))
                if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
                if t.astimezone(tz).date().isoformat()==session_date:
                    day.append(r)
            except Exception:
                pass

        def avg(rows,col):
            vals=[_f(r.get(col),float('nan')) for r in rows if r.get(col) is not None]
            vals=[v for v in vals if not math.isnan(v)]
            return round(sum(vals)/len(vals),3) if vals else None

        def hit(rows,col):
            vals=[_f(r.get(col)) for r in rows if r.get(col) is not None]
            return round(sum(1 for v in vals if v>0)/len(vals)*100,1) if vals else None

        symbol_rows=[]
        for sym in sorted({str(r.get('symbol') or '') for r in day if r.get('symbol')}):
            g=[r for r in day if str(r.get('symbol') or '')==sym]
            symbol_rows.append({
                'symbol':sym,'samples':len(g),
                'ret_5m':avg(g,'ret_5m'),'ret_15m':avg(g,'ret_15m'),
                'ret_30m':avg(g,'ret_30m'),'ret_60m':avg(g,'ret_60m'),
                'hit_60_pct':hit(g,'ret_60m'),
                'mfe_pct':avg(g,'mfe_pct'),'mae_pct':avg(g,'mae_pct'),
                'avg_power':avg(g,'power'),
                'max_power':round(max((_f(r.get('power')) for r in g),default=0),1),
                'avg_power_delta':avg(g,'power_delta'),
            })
        symbol_rows.sort(
            key=lambda r:(r.get('ret_60m') is not None,r.get('ret_60m') or -999),
            reverse=True
        )

        power_rows=[]
        buckets=[
            ('≤0',-1e9,0),('0~20',0,20),('20~40',20,40),
            ('40~60',40,60),('60+',60,1e9)
        ]
        for label,lo,hi in buckets:
            g=[r for r in day if lo <= _f(r.get('power')) < hi]
            if not g:continue
            power_rows.append({
                'power_bucket':label,'samples':len(g),
                'ret_5m':avg(g,'ret_5m'),'ret_15m':avg(g,'ret_15m'),
                'ret_30m':avg(g,'ret_30m'),'ret_60m':avg(g,'ret_60m'),
                'hit_60_pct':hit(g,'ret_60m'),
                'mfe_pct':avg(g,'mfe_pct'),'mae_pct':avg(g,'mae_pct')
            })

        complete60=sum(1 for r in day if r.get('ret_60m') is not None)
        return {
            'market':'KOREA','session_date':session_date,
            'samples':len(day),'complete_60':complete60,
            'symbols':len(symbol_rows),
            'ret_5m':avg(day,'ret_5m'),'ret_15m':avg(day,'ret_15m'),
            'ret_30m':avg(day,'ret_30m'),'ret_60m':avg(day,'ret_60m'),
            'hit_60_pct':hit(day,'ret_60m'),
            'mfe_pct':avg(day,'mfe_pct'),'mae_pct':avg(day,'mae_pct'),
            'by_symbol':symbol_rows,'by_power':power_rows,
            'note':'KR observational validation from Tracker snapshots. No verified KR 1m/5m Setup/Trigger gate yet.'
        }

    def update_validation_outcomes(self,market,symbol,current_price):
        if not current_price:return
        now_dt=datetime.now(timezone.utc)
        with self._c() as c:
            rows=c.execute("SELECT * FROM v4_validation_marks WHERE market=? AND symbol=? AND (ret_60m IS NULL OR settled_at IS NULL) ORDER BY id DESC LIMIT 240",(market.upper(),symbol.upper())).fetchall()
            for r in rows:
                try:
                    ts=datetime.fromisoformat(str(r['ts']).replace('Z','+00:00')); ts=ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                except Exception:continue
                anchor=_f(r['anchor_price'])
                if not anchor:continue
                mins=max(0,(now_dt-ts).total_seconds()/60); ret=(current_price/anchor-1)*100
                vals={'mfe_pct':max(_f(r['mfe_pct']),ret),'mae_pct':min(_f(r['mae_pct']),ret)}
                if mins>=5 and r['ret_5m'] is None:vals['ret_5m']=ret
                if mins>=15 and r['ret_15m'] is None:vals['ret_15m']=ret
                if mins>=30 and r['ret_30m'] is None:vals['ret_30m']=ret
                if mins>=60 and r['ret_60m'] is None:vals['ret_60m']=ret; vals['settled_at']=_now()
                c.execute(f"UPDATE v4_validation_marks SET {','.join(f'{k}=?' for k in vals)} WHERE id=?",list(vals.values())+[r['id']])

    def validation_episodes(self,market=None,limit=5000,bridge_minutes=5):
        # Derive signal episodes from minute validation snapshots.
        rows=self.validation_marks(market,limit)
        if not rows:return []

        active={'SETUP','READY','ENTRY','HOLD','PARTIAL_EXIT','EXIT_READY','HARD_EXIT'}
        level={'SETUP':1,'READY':2,'ENTRY':3,'HOLD':4,'PARTIAL_EXIT':5,'EXIT_READY':6,'HARD_EXIT':7}
        rows=sorted(rows,key=lambda r:(str(r.get('symbol') or ''),str(r.get('ts') or '')))
        current={}; episodes=[]

        def dt(v):
            try:
                x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
                return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
            except Exception:return None

        def finish(sym,cur):
            if not cur:return
            start=cur['start']
            first_ts=dt(start.get('ts')); last_ts=dt(cur.get('last_active_ts'))
            duration=((last_ts-first_ts).total_seconds()/60) if first_ts and last_ts else 0.0
            path=cur.get('path') or []
            max_state=max(path,key=lambda s:level.get(s,0)) if path else str(start.get('state') or 'SETUP')
            episodes.append({
                'market':start.get('market'),'symbol':sym,
                'start_ts':start.get('ts'),'end_ts':cur.get('last_active_ts'),
                'duration_min':round(max(0.0,duration),1),
                'start_state':start.get('state'),'max_state':max_state,
                'state_path':' → '.join(path),
                'anchor_price':start.get('anchor_price'),
                'start_power':start.get('power'),'max_power':cur.get('max_power'),
                'start_power_delta':start.get('power_delta'),
                'finder_rank':start.get('finder_rank'),
                'start_setup':start.get('setup_count'),'max_setup':cur.get('max_setup'),
                'start_trigger':start.get('trigger_count'),'max_trigger':cur.get('max_trigger'),
                'rvol':start.get('rvol'),'volume_ratio':start.get('volume_ratio'),
                'ret_5m':start.get('ret_5m'),'ret_15m':start.get('ret_15m'),
                'ret_30m':start.get('ret_30m'),'ret_60m':start.get('ret_60m'),
                'mfe_pct':start.get('mfe_pct'),'mae_pct':start.get('mae_pct'),
                'settled_at':start.get('settled_at'),
                'settled':start.get('ret_60m') is not None,
            })

        for r in rows:
            sym=str(r.get('symbol') or '').upper()
            if not sym:continue
            state=str(r.get('state') or 'WATCH').upper()
            ts=dt(r.get('ts')); cur=current.get(sym)

            if state in active:
                if cur:
                    last=dt(cur.get('last_active_ts'))
                    gap=((ts-last).total_seconds()/60) if ts and last else 999
                    if gap>bridge_minutes:
                        finish(sym,cur); cur=None
                if not cur:
                    cur={'start':r,'last_active_ts':r.get('ts'),'path':[state],
                         'max_power':_f(r.get('power')),
                         'max_setup':int(_f(r.get('setup_count'))),
                         'max_trigger':int(_f(r.get('trigger_count')))}
                else:
                    cur['last_active_ts']=r.get('ts')
                    if not cur['path'] or cur['path'][-1]!=state:cur['path'].append(state)
                    cur['max_power']=max(_f(cur.get('max_power')),_f(r.get('power')))
                    cur['max_setup']=max(int(_f(cur.get('max_setup'))),int(_f(r.get('setup_count'))))
                    cur['max_trigger']=max(int(_f(cur.get('max_trigger'))),int(_f(r.get('trigger_count'))))
                current[sym]=cur
            elif cur:
                last=dt(cur.get('last_active_ts'))
                gap=((ts-last).total_seconds()/60) if ts and last else 999
                if gap>bridge_minutes:
                    finish(sym,cur); current.pop(sym,None)

        for sym,cur in list(current.items()):finish(sym,cur)
        episodes.sort(key=lambda x:str(x.get('start_ts') or ''),reverse=True)
        for i,e in enumerate(episodes,1):e['episode_id']=i
        return episodes

    def validation_stage_anchors(self,market=None,limit=5000,bridge_minutes=5):
        # First SETUP / READY / ENTRY mark inside each derived Episode.
        marks=self.validation_marks(market,limit)
        episodes=self.validation_episodes(market,limit,bridge_minutes)
        if not marks or not episodes:return []

        def dt(v):
            try:
                x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
                return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
            except Exception:return None

        by_symbol={}
        for r in marks:
            sym=str(r.get('symbol') or '').upper()
            if sym:by_symbol.setdefault(sym,[]).append(r)
        for sym in by_symbol:
            by_symbol[sym].sort(key=lambda r:str(r.get('ts') or ''))

        out=[]
        targets=('SETUP','READY','ENTRY')
        for ep in episodes:
            sym=str(ep.get('symbol') or '').upper()
            start=dt(ep.get('start_ts')); end=dt(ep.get('end_ts'))
            if not sym or not start or not end:continue
            window=[]
            for r in by_symbol.get(sym,[]):
                t=dt(r.get('ts'))
                if t and start<=t<=end:window.append(r)
            if not window:continue

            for stage in targets:
                hit=next((r for r in window if str(r.get('state') or '').upper()==stage),None)
                if not hit:continue
                ht=dt(hit.get('ts'))
                delay=((ht-start).total_seconds()/60) if ht else 0.0
                out.append({
                    'episode_id':ep.get('episode_id'),
                    'market':hit.get('market'),
                    'symbol':sym,
                    'stage':stage,
                    'stage_ts':hit.get('ts'),
                    'minutes_from_episode_start':round(max(0.0,delay),1),
                    'anchor_price':hit.get('anchor_price'),
                    'power':hit.get('power'),
                    'power_delta':hit.get('power_delta'),
                    'finder_rank':hit.get('finder_rank'),
                    'setup_count':hit.get('setup_count'),
                    'trigger_count':hit.get('trigger_count'),
                    'rvol':hit.get('rvol'),
                    'volume_ratio':hit.get('volume_ratio'),
                    'ret_5m':hit.get('ret_5m'),
                    'ret_15m':hit.get('ret_15m'),
                    'ret_30m':hit.get('ret_30m'),
                    'ret_60m':hit.get('ret_60m'),
                    'mfe_pct':hit.get('mfe_pct'),
                    'mae_pct':hit.get('mae_pct'),
                    'settled_at':hit.get('settled_at'),
                    'settled':hit.get('ret_60m') is not None,
                })

        out.sort(key=lambda r:str(r.get('stage_ts') or ''),reverse=True)
        return out

    def validation_entry_shadow(self,market=None,limit=5000,bridge_minutes=5):
        """Episode-deduplicated threshold shadow test.

        Uses the FIRST qualifying validation mark per Episode for each profile.
        It never changes live ENTRY logic and never creates an order.

        Grid trigger count is recomputed self-consistently:
        green + break_prev_high + volume_expansion + one_min_impulse
        + dynamic Power acceleration for that profile.
        """
        episodes=self.validation_episodes(market,limit,bridge_minutes)
        if not episodes:return {'grid':[],'current_core':None,'current_ready':None,'anchors':0}

        sql='SELECT * FROM v4_validation_marks'; args=[]
        if market:sql+=' WHERE market=?'; args=[market.upper()]
        sql+=' ORDER BY ts ASC'
        with self._c() as c:
            marks=[dict(r) for r in c.execute(sql,args).fetchall()]

        def dt(v):
            try:
                x=datetime.fromisoformat(str(v).replace('Z','+00:00'))
                return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
            except Exception:return None

        def session_day(r):
            t=r.get('_ts') or dt(r.get('ts'))
            if not t:return 'UNKNOWN'
            try:
                tz=ZoneInfo('America/New_York' if str(r.get('market') or market or '').upper()=='USA' else 'Asia/Seoul')
                return t.astimezone(tz).date().isoformat()
            except Exception:
                return t.date().isoformat()

        def session_summary(hits):
            groups={}
            for h in hits:
                groups.setdefault(session_day(h),[]).append(h)
            out=[]
            for day,rows in sorted(groups.items()):
                def avg(col):
                    vals=[]
                    for x in rows:
                        if x.get(col) is None:continue
                        v=_f(x.get(col),float('nan'))
                        if not math.isnan(v):vals.append(v)
                    return round(sum(vals)/len(vals),3) if vals else None
                r60=[_f(x.get('ret_60m')) for x in rows if x.get('ret_60m') is not None]
                out.append({'session_date':day,'episodes':len(rows),'complete_60':len(r60),
                            'ret_15m':avg('ret_15m'),'ret_30m':avg('ret_30m'),'ret_60m':avg('ret_60m'),
                            'hit_60_pct':round(sum(1 for x in r60 if x>0)/len(r60)*100,1) if r60 else None,
                            'mfe_pct':avg('mfe_pct'),'mae_pct':avg('mae_pct')})
            return out

        parsed=[]
        for r in marks:
            try:feat=json.loads(r.get('feature_json') or '{}')
            except Exception:feat={}
            gate=feat.get('entry_gate') or {}
            checks=gate.get('trigger_checks') or {}
            r['_setup_ok']=bool(gate.get('setup_ok'))
            r['_chase_ok']=bool(gate.get('chase_ok'))
            r['_ready']=bool(gate.get('ready'))
            r['_entry']=bool(gate.get('entry'))
            r['_green']=bool(checks.get('green_1m'))
            r['_break']=bool(checks.get('break_prev_high'))
            r['_volume']=bool(checks.get('volume_expansion'))
            r['_impulse']=bool(checks.get('one_min_impulse'))
            r['_ts']=dt(r.get('ts'))
            parsed.append(r)

        by_symbol={}
        for r in parsed:
            sym=str(r.get('symbol') or '').upper()
            if sym:by_symbol.setdefault(sym,[]).append(r)

        windows=[]
        for ep in episodes:
            sym=str(ep.get('symbol') or '').upper()
            st=dt(ep.get('start_ts')); en=dt(ep.get('end_ts'))
            if not sym or not st or not en:continue
            rows=[r for r in by_symbol.get(sym,[]) if r.get('_ts') and st<=r['_ts']<=en]
            if rows:windows.append((ep,rows))

        def summarize(hits,name,meta=None):
            if not hits:return {
                'profile':name,'episodes':0,'complete_60':0,
                'ret_5m':None,'ret_15m':None,'ret_30m':None,'ret_60m':None,
                'hit_60_pct':None,'mfe_pct':None,'mae_pct':None,**(meta or {})
            }
            def avg(col):
                v=[_f(x.get(col),float('nan')) for x in hits if x.get(col) is not None]
                v=[x for x in v if not math.isnan(x)]
                return round(sum(v)/len(v),3) if v else None
            r60=[_f(x.get('ret_60m')) for x in hits if x.get('ret_60m') is not None]
            return {
                'profile':name,
                'episodes':len(hits),
                'complete_60':len(r60),
                'ret_5m':avg('ret_5m'),'ret_15m':avg('ret_15m'),
                'ret_30m':avg('ret_30m'),'ret_60m':avg('ret_60m'),
                'hit_60_pct':round(sum(1 for x in r60 if x>0)/len(r60)*100,1) if r60 else None,
                'mfe_pct':avg('mfe_pct'),'mae_pct':avg('mae_pct'),
                **(meta or {})
            }

        # Actual live definitions, evaluated from stored entry_gate booleans.
        ready_hits=[]; entry_hits=[]
        for ep,rows in windows:
            rr=next((r for r in rows if r.get('_ready')),None)
            ee=next((r for r in rows if r.get('_entry')),None)
            if rr:ready_hits.append(rr)
            if ee:entry_hits.append(ee)

        current_ready=summarize(ready_hits,'CURRENT_READY',{'rule':'stored gate.ready'})
        current_core=summarize(entry_hits,'CURRENT_CORE',{'rule':'stored gate.entry'})

        grid=[]
        powers=(55,60,65,68)
        triggers=(3,4,5)
        deltas=(0,2,4)
        for pmin in powers:
            for tmin in triggers:
                for dmin in deltas:
                    hits=[]
                    core_pass=0
                    for ep,rows in windows:
                        hit=None
                        for r in rows:
                            if not r.get('_setup_ok') or not r.get('_chase_ok'):continue
                            power=_f(r.get('power')); delta=_f(r.get('power_delta'))
                            accel=bool(power>=pmin and delta>=dmin)
                            nonpower=sum(1 for k in ('_green','_break','_volume','_impulse') if r.get(k))
                            trig=nonpower+(1 if accel else 0)
                            if accel and trig>=tmin:
                                hit=r; break
                        if hit:
                            hits.append(hit)
                            if hit.get('_green') and hit.get('_break') and hit.get('_volume'):
                                core_pass+=1
                    stats=session_summary(hits)
                    meta={
                        'power_min':pmin,'trigger_min':tmin,'delta_min':dmin,
                        'core_pass_pct':round(core_pass/len(hits)*100,1) if hits else None,
                        'session_stats':stats,'session_count':len(stats)
                    }
                    grid.append(summarize(hits,f'P{pmin}/T{tmin}/D{dmin}',meta))

        grid.sort(key=lambda x:(
            -(x.get('complete_60') or 0),
            -(x.get('ret_30m') if x.get('ret_30m') is not None else -999)
        ))
        return {
            'grid':grid,
            'current_core':current_core,
            'current_ready':current_ready,
            'episode_windows':len(windows),
            'note':'Shadow only. First qualifying mark per Episode. No live rule or order behavior is changed.'
        }

    def validation_marks(self,market=None,limit=1000):
        sql='SELECT id,ts,market,symbol,state,anchor_price,power,power_delta,finder_rank,setup_count,trigger_count,rvol,volume_ratio,hard_floor,warning_floor,floor_mode,ret_5m,ret_15m,ret_30m,ret_60m,mfe_pct,mae_pct,settled_at FROM v4_validation_marks'; args=[]
        if market:sql+=' WHERE market=?'; args=[market.upper()]
        sql+=' ORDER BY id DESC LIMIT ?'; args.append(int(limit))
        with self._c() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]

class CleanEngine:
    def __init__(self,db_path):
        self.store=V4Store(db_path); self.finder={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self.tracker={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self._last={}; self._snap={}; self._rank={}; self._kr_gate_cache={}; self._lock=threading.RLock(); self._williams_mock_account_synced=False; self.paper=PaperBroker(db_path)
    def build_usa_finder(self,candidates,discovery,limit=5,db=None,commit=True,shadow_allow_unknown_quality=False,shadow_min_recent_bars=0):
        qmap={str(r.get('symbol') or '').upper():r for r in (discovery.get('rows') or [])}
        rows=[]
        inverse_syms={'SOXS','SQQQ'}



        # V4.8.11 ETF PAIR BRIDGE LIVE

        # Give benchmark, leveraged, and inverse ETFs an equal chance

        # to enter the existing Finder scoring pipeline.

        if db is not None and _session('USA')=='REGULAR':

            try:

                _existing={

                    str(x.get('symbol') or '').upper()

                    for x in (candidates or [])

                }

                _qmap={

                    str(x.get('symbol') or '').upper():x

                    for x in db.quotes()

                }

                _mmap={

                    str(x.get('symbol') or '').upper():x

                    for x in db.daily_metrics()

                }



                candidates=list(candidates or [])



                for _sym in ('QQQ','TQQQ','SQQQ','SMH','SOXL','SOXS'):

                    if _sym in _existing:

                        continue



                    _q=_qmap.get(_sym) or {}

                    _m=_mmap.get(_sym) or {}



                    _price=_f(_q.get('price'))

                    _chg=_f(_q.get('change_pct'))

                    _vol=_f(_q.get('volume'))



                    _atr=_f(

                        _m.get('atr_pct')

                        or _m.get('atr5_pct')

                        or _m.get('atr')

                    )



                    _avgvol=_f(

                        _m.get('avg_volume')

                        or _m.get('avg5_volume')

                        or _m.get('avg20_volume')

                    )



                    if _price<=0 or _vol<=0:

                        continue



                    if _atr<=0:

                        _atr=2.0



                    _rvol=(_vol/_avgvol) if _avgvol>0 else 1.0



                    candidates.append({

                        'symbol':_sym,

                        'price':_price,

                        'change_pct':_chg,

                        'dollar_volume':_price*_vol,

                        'rvol':_rvol,

                        'atr_pct':_atr,

                        'score':50.0,

                        'eligible':True,

                        'core_etf_bridge':True,

                    })

            except Exception:

                pass


        regime='NEUTRAL'
        for c in candidates or []:
            if c.get('market_regime'):
                regime=str(c.get('market_regime')).upper()
                break

        def recent_leadership(sym):
            if db is None:
                return {
                    'ret_1m':0.0,'ret_3m':0.0,'ret_5m':0.0,'ret_15m':0.0,
                    'vol_accel':1.0,'recent_vol_3m':0.0,'prior_vol_median_10m':0.0,'volume_coverage_10m':0.0,
                    'break_3m_high':False,'fresh_score':0.0,
                    'fresh_mover':False,'fresh_mode':'WATCH','bars':0,'score':0.0
                }
            try:
                ticks=db.ticks(sym,2500)
                b=ticks_to_bars(ticks,1)
                if len(b)<6:
                    return {
                        'ret_1m':0.0,'ret_3m':0.0,'ret_5m':0.0,'ret_15m':0.0,
                        'vol_accel':1.0,'recent_vol_3m':0.0,'prior_vol_median_10m':0.0,'volume_coverage_10m':0.0,
                        'break_3m_high':False,'fresh_score':0.0,
                        'fresh_mover':False,'fresh_mode':'WATCH','bars':len(b),'score':0.0
                    }
                b=b.tail(25).copy()
                close=pd.to_numeric(b['close'],errors='coerce')
                volume=pd.to_numeric(b['volume'],errors='coerce').fillna(0)
                last=_f(close.iloc[-1])
                p1=_f(close.iloc[-2]) if len(close)>=2 else last
                p3=_f(close.iloc[-4]) if len(close)>=4 else last
                p5=_f(close.iloc[-6]) if len(close)>=6 else last
                p15=_f(close.iloc[-16]) if len(close)>=16 else _f(close.iloc[0])
                r1=(last/p1-1)*100 if p1>0 else 0.0
                r3=(last/p3-1)*100 if p3>0 else 0.0
                r5=(last/p5-1)*100 if p5>0 else 0.0
                r15=(last/p15-1)*100 if p15>0 else 0.0

                recent_vol=_f(volume.tail(3).mean(),0)
                if len(volume)>=13:
                    prior_slice=volume.iloc[-13:-3]
                else:
                    prior_slice=volume.iloc[:-3]

                prior_pos=prior_slice[prior_slice>0]
                coverage=(len(prior_pos)/max(len(prior_slice),1)) if len(prior_slice) else 0.0

                if len(prior_pos)>=4:
                    prior_med=_f(prior_pos.median(),0)
                    prior_mean=_f(prior_pos.mean(),0)
                    # Median is robust, mean*0.40 prevents a tiny median after sparse synthetic bars.
                    prior_vol=max(prior_med,prior_mean*0.40,1.0)
                    vacc=recent_vol/prior_vol
                else:
                    prior_vol=0.0
                    vacc=1.0

                # Sparse volume history is not trustworthy enough for a "burst" claim.
                if coverage<0.50:
                    vacc=min(vacc,1.25)

                vacc=_clip(vacc,0,6)

                highs=pd.to_numeric(b['high'],errors='coerce')
                prev3_high=_f(highs.iloc[-4:-1].max(),0) if len(highs)>=4 else 0
                break3=bool(prev3_high>0 and last>prev3_high)

                # V4.4.5 Fresh Momentum V2
                # Two valid paths:
                # A) CONTINUATION: 3m + 5m acceleration with participation.
                # B) BREAKOUT:    1m impulse + local high breakout with participation.
                # break3 is no longer mandatory for a steady 3m/5m acceleration.
                fresh=0.0

                if r1>=0.35: fresh+=9
                elif r1>=0.18: fresh+=6
                elif r1>=0.08: fresh+=3
                elif r1<=-0.20: fresh-=6

                if r3>=1.00: fresh+=12
                elif r3>=0.55: fresh+=9
                elif r3>=0.25: fresh+=6
                elif r3<=-0.55: fresh-=8
                elif r3<0: fresh-=3

                if r5>=1.25: fresh+=9
                elif r5>=0.65: fresh+=6
                elif r5>=0.30: fresh+=4
                elif r5<0: fresh-=3

                if break3:
                    fresh+=6

                if vacc>=3.0: fresh+=8
                elif vacc>=2.0: fresh+=6
                elif vacc>=1.5: fresh+=4
                elif vacc>=1.15: fresh+=2
                elif vacc<0.55: fresh-=2

                if r1>0 and r3>0 and r5>0:
                    fresh+=3

                fresh=_clip(fresh,-15,40)

                continuation_path=bool(
                    coverage>=0.50 and
                    r3>=0.25 and
                    r5>=0.30 and
                    vacc>=1.10 and
                    fresh>=15
                )
                breakout_path=bool(
                    coverage>=0.50 and
                    r1>=0.12 and
                    r3>=0.18 and
                    break3 and
                    vacc>=1.15 and
                    fresh>=15
                )

                fresh_mover=bool(continuation_path or breakout_path)
                fresh_mode=(
                    'CONTINUATION' if continuation_path
                    else 'BREAKOUT' if breakout_path
                    else 'WATCH'
                )

                lead=0.0
                # Reward what is moving NOW, not only what moved earlier today.
                if r5>=3: lead+=24
                elif r5>=1.5: lead+=18
                elif r5>=0.7: lead+=12
                elif r5>=0.25: lead+=6
                elif r5<=-1.5: lead-=24
                elif r5<=-0.7: lead-=15
                elif r5<=-0.25: lead-=7

                if r15>=5: lead+=18
                elif r15>=2.5: lead+=13
                elif r15>=1: lead+=8
                elif r15<=-2.5: lead-=16
                elif r15<=-1: lead-=9

                if vacc>=3: lead+=10
                elif vacc>=1.8: lead+=7
                elif vacc>=1.25: lead+=3
                elif vacc<0.65: lead-=3

                return {
                    'ret_1m':round(r1,3),'ret_3m':round(r3,3),
                    'ret_5m':round(r5,3),'ret_15m':round(r15,3),
                    'vol_accel':round(vacc,2),
                    'recent_vol_3m':round(recent_vol,2),
                    'prior_vol_median_10m':round(prior_vol,2),
                    'volume_coverage_10m':round(coverage,2),
                    'break_3m_high':break3,
                    'fresh_score':round(fresh,1),
                    'fresh_mover':fresh_mover,
                    'fresh_mode':fresh_mode,
                    'bars':len(b),'score':round(_clip(lead,-35,40),1)
                }
            except Exception:
                return {
                    'ret_1m':0.0,'ret_3m':0.0,'ret_5m':0.0,'ret_15m':0.0,
                    'vol_accel':1.0,'recent_vol_3m':0.0,'prior_vol_median_10m':0.0,'volume_coverage_10m':0.0,
                    'break_3m_high':False,'fresh_score':0.0,
                    'fresh_mover':False,'fresh_mode':'WATCH','bars':0,'score':0.0
                }

        for c in candidates or []:
            sym=str(c.get('symbol') or '').upper(); q=qmap.get(sym) or {}; quality=q.get('quality_grade')
            shadow_quality_unknown=False
            if quality not in ('A','B_EVENT','C_HIGH_RISK'):
                # V4.6.2 Discovery Bridge Shadow:
                # production still rejects rows without verified discovery quality.
                # Shadow may evaluate Screener-eligible misses conservatively with
                # zero quality bonus. This never mutates live Finder when commit=False.
                if shadow_allow_unknown_quality and bool(c.get('eligible')):
                    quality='SHADOW_UNKNOWN'
                    shadow_quality_unknown=True
                else:
                    continue
            price=_f(c.get('price')); dv=_f(c.get('dollar_volume')); rvol=_f(c.get('rvol')); atr=abs(_f(c.get('atr_pct'))); chg=_f(c.get('change_pct'))
            # V4.8.12 Leveraged ETF ATR Cap
            atr_cap=18 if sym in {'SOXL','SOXS'} else 12
            if price<5 or dv<20_000_000 or atr<=0 or atr>atr_cap:continue
            liq=_clip((math.log10(max(dv,1))-7.3)/2*25,0,25)
            act=_clip((rvol-.5)/2.5*25,0,25)
            vol=20 if 2<=atr<=7 else 14 if 1<=atr<=10 else 7
            directional=_clip(abs(chg)/8*15,0,15)
            qp=15 if quality=='A' else 9 if quality=='B_EVENT' else 0
            chase=18 if quality=='C_HIGH_RISK' else 12 if abs(chg)>=15 else 6 if abs(chg)>=10 else 0
            base=_clip(liq+act+vol+directional+qp-chase,0,100)

            # Make Finder responsive to the current tape instead of mostly static
            # liquidity/ATR characteristics.
            live_score=_f(c.get('score'),base)

            # V4.4: daily/live screener remains the base, but actual last 5m/15m
            # leadership can promote a fresh mover or demote a fading earlier winner.
            # V4.4.8 Finder Score Calibration
            # Keep selection responsive, but prevent Recent + Fresh from stacking
            # into easy 100-point saturation.
            live_component=.58*live_score
            base_component=.22*base
            recent=recent_leadership(sym)
            recent_component=.72*_f(recent.get('score'))
            score=live_component+base_component+recent_component

            # Fresh is a promotion signal, not an independent 40-point score.
            # Full Fresh remains meaningful, but is capped so one short burst
            # cannot make the Finder read like a probability.
            raw_fresh=max(0.0,_f(recent.get('fresh_score')))
            if recent.get('fresh_mover'):
                fresh_bonus=min(18.0,raw_fresh*0.65)
            else:
                # WATCH diagnostics remain small context only.
                fresh_bonus=min(3.0,raw_fresh*0.15)
            score+=fresh_bonus

            # We do not auto-short common stocks. A negative common-stock move should not
            # dominate the actionable TOP5 merely because its absolute move is large.
            down_penalty=0.0
            if sym not in inverse_syms and chg < -0.30:
                down_penalty=min(28.0,abs(chg)*3.0)
                score-=down_penalty

            inverse_bonus=0.0
            if regime in ('BEAR','STRONG_BEAR') and sym in inverse_syms and chg>0:
                inverse_bonus=10 if regime=='BEAR' else 16
                score+=inverse_bonus

            # If this name was recently in Heavy Tracker, use its measured Power
            # to prevent an earlier daily winner from staying near the top while fading now.
            prev_power_row=self._last.get(('POWER','USA',sym))
            observed_power=_f((prev_power_row or {}).get('power')) if prev_power_row else None
            fade_penalty=0.0
            if recent.get('bars',0)>=6:
                r5=_f(recent.get('ret_5m'))
                if r5 < -0.25:
                    fade_penalty += min(14.0, 5.0 + abs(r5)*5.0)
                if observed_power is not None and observed_power < 0 and r5 < 0:
                    fade_penalty += min(18.0, 8.0 + abs(observed_power)*0.22)
            score-=fade_penalty

            # Preserve a raw diagnostic score, then compress only the crowded high end.
            # Monotonic compression keeps ordering while restoring score separation.
            raw_score=score
            if score>85:
                score=85+(score-85)*0.35
            score=_clip(score,0,96)

            reason=(
                f"live {live_component:.1f} + base {base_component:.1f}"
                f" + recent {recent_component:+.1f}"
            )
            if fresh_bonus:
                reason+=f" + fresh {fresh_bonus:.1f}/{recent.get('fresh_mode','WATCH')}"
            if recent.get('bars',0)>=6:
                reason+=(
                    f" (1m {recent['ret_1m']:+.2f}% / 3m {recent['ret_3m']:+.2f}%"
                    f" / 5m {recent['ret_5m']:+.2f}% / vol×{recent['vol_accel']:.1f}"
                    f" / break3 {'Y' if recent['break_3m_high'] else 'N'})"
                )
            if fade_penalty:
                reason+=f" - fading {fade_penalty:.0f}"
            if down_penalty:
                reason+=f" - long-only down {down_penalty:.0f}"
            if inverse_bonus:
                reason+=f" + {regime} inverse {inverse_bonus:.0f}"

            extreme_continue=True
            if quality=='C_HIGH_RISK':
                # Extreme movers are visible in Light20, but Heavy5 requires evidence
                # that the move is still alive NOW. Thresholds are hypotheses for validation.
                extreme_continue=bool(
                    (
                        recent.get('bars',0)>=6 and
                        _f(recent.get('ret_5m'))>=0.25 and
                        _f(recent.get('vol_accel'),1)>=1.10 and
                        fade_penalty<=3
                    )
                    or bool(recent.get('fresh_mover'))
                )
                reason+=(" + extreme continuing" if extreme_continue else " + extreme watch-only")

            rows.append({
                'market':'USA','symbol':sym,'name':q.get('name') or c.get('name') or sym,
                'quality':quality,'finder_score':round(score,1),
                'finder_raw_score':round(raw_score,1),
                'score_components':{
                    'live':round(live_component,1),
                    'base':round(base_component,1),
                    'recent':round(recent_component,1),
                    'fresh':round(fresh_bonus,1),
                    'fade':round(-fade_penalty,1),
                    'down':round(-down_penalty,1),
                    'inverse':round(inverse_bonus,1),
                },
                'direction':'UP' if chg>=0 else 'DOWN','price':price,'change_pct':chg,
                'dollar_volume':dv,'rvol':rvol,'atr_pct':atr,
                'risk':'EXTREME' if quality=='C_HIGH_RISK' else 'CHASE' if chase else 'NORMAL',
                'market_regime':regime,'finder_reason':reason,
                'ret_1m':recent.get('ret_1m'),'ret_3m':recent.get('ret_3m'),
                'ret_5m':recent.get('ret_5m'),'ret_15m':recent.get('ret_15m'),
                'volume_accel':recent.get('vol_accel'),'recent_score':recent.get('score'),
                'break_3m_high':recent.get('break_3m_high'),
                'fresh_score':recent.get('fresh_score'),
                'fresh_mover':recent.get('fresh_mover'),
                'fresh_mode':recent.get('fresh_mode'),
                'recent_vol_3m':recent.get('recent_vol_3m'),
                'prior_vol_median_10m':recent.get('prior_vol_median_10m'),
                'volume_coverage_10m':recent.get('volume_coverage_10m'),
                'observed_power':observed_power,'fade_penalty':round(fade_penalty,1),
                'recent_bars':recent.get('bars'),
                'extreme_continue':extreme_continue,
                'extreme_watch':quality=='C_HIGH_RISK',
                'shadow_quality_unknown':shadow_quality_unknown,
                'source_origin':q.get('origin'),
                'inverse_candidate':sym in inverse_syms
            })

        # Light Tracker 20:
        # broad/daily screening first, then live 5m/15m leadership + fade controls.
        rows.sort(
            key=lambda r:(
                r['finder_score'],
                1 if r.get('fresh_mover') else 0,
                _f(r.get('fresh_score')),
                r['recent_score'],
                r['dollar_volume']
            ),
            reverse=True
        )
        heavy_light_rows=rows[:20]
        light_rows=rows[:40]
        for i,r in enumerate(light_rows,1):
            r['light_rank']=i

        # Heavy Tracker receives the best 5 actionable names from Light20.
        # C_HIGH_RISK names remain visible in Light20; they can enter Heavy5 only
        # while the strict continuation test is true.
        heavy_pool=[
            r for r in heavy_light_rows
            if r.get('quality')!='C_HIGH_RISK' or r.get('extreme_continue')
        ]
        # V4.6.2.1 fair-comparison guard:
        # unknown-quality Bridge rows may appear in Light diagnostics immediately,
        # but cannot enter Shadow Finder until enough 1m bars exist.
        if not commit and int(shadow_min_recent_bars or 0)>0:
            heavy_pool=[
                r for r in heavy_pool
                if (not r.get('shadow_quality_unknown'))
                or int(r.get('recent_bars') or 0)>=int(shadow_min_recent_bars)
            ]
        selected=heavy_pool[:limit]

        # A bearish regime must not silently hide a qualified inverse ETF.
        if regime in ('BEAR','STRONG_BEAR'):
            inv=[r for r in light_rows if r.get('inverse_candidate') and r.get('change_pct',0)>0 and r.get('finder_score',0)>=35]
            inv.sort(key=lambda r:(r['finder_score'],r['recent_score'],r['dollar_volume']),reverse=True)
            if inv and inv[0]['symbol'] not in {r['symbol'] for r in selected}:
                selected=selected[:max(0,limit-1)]+[inv[0]]
                selected.sort(key=lambda r:(r['finder_score'],r['recent_score'],r['dollar_volume']),reverse=True)

        selected=selected[:limit]
        for i,r in enumerate(selected,1):r['rank']=i

        result={
            'rows':selected,
            'updated_at':_now(),
            'session':_session('USA'),
            'light_rows':light_rows,
            'light_count':len(light_rows),
            'rotation_seconds':30,
            'market_regime':regime,
            'preferred_direction':'INVERSE' if regime in ('BEAR','STRONG_BEAR') else 'LONG',
            'shadow':not commit,
        }
        if not commit:
            return result

        self._update_finder('USA',selected)
        self.finder['USA']['light_rows']=light_rows
        self.finder['USA']['light_count']=len(light_rows)
        self.finder['USA']['rotation_seconds']=30
        self.finder['USA']['market_regime']=regime
        self.finder['USA']['preferred_direction']='INVERSE' if regime in ('BEAR','STRONG_BEAR') else 'LONG'
        return self.finder['USA']
    def build_korea_finder(self,discovery,limit=5):
        rows=[]
        for r in discovery.get('rows') or []:
            q=r.get('quality_grade'); risk=str(r.get('chase_risk') or 'NORMAL').upper()
            if q not in ('A','B_EVENT') or risk=='EXTREME':continue
            rows.append({'market':'KOREA','symbol':str(r.get('symbol') or ''),'name':r.get('name') or r.get('symbol'),'quality':q,'finder_score':round(_f(r.get('score')),1),'direction':str(r.get('bias') or 'NEUTRAL').upper(),'price':_f(r.get('price')),'change_pct':_f(r.get('change_pct')),'dollar_volume':_f(r.get('trading_value')),'rvol':None,'atr_pct':None,'risk':risk})
        rows.sort(key=lambda x:x['finder_score'],reverse=True); rows=rows[:limit]
        for i,r in enumerate(rows,1):r['rank']=i
        self._update_finder('KOREA',rows); return self.finder['KOREA']
    def _update_finder(self,market,rows):
        old={r['symbol']:r.get('rank') for r in self.finder[market].get('rows',[])}; new={r['symbol']:r.get('rank') for r in rows}
        for sym,rank in new.items():
            o=old.get(sym)
            if o is None:self.store.event(market,sym,'TOP5_IN',rank_to=rank,message=f'{sym} TOP5 신규 진입')
            elif o!=rank and abs(o-rank)>=2:self.store.event(market,sym,'RANK_MOVE',rank_from=o,rank_to=rank,message=f'{sym} 순위 {o}→{rank}')
        for sym,rank in old.items():
            if sym not in new:self.store.event(market,sym,'TOP5_OUT',rank_from=rank,message=f'{sym} TOP5 탈락')
        self.finder[market]={'rows':rows,'updated_at':_now(),'session':_session(market)}
    def tracked_symbols(self,market):
        pos=[p['symbol'] for p in self.store.positions(market)]; find=[r['symbol'] for r in self.finder[market]['rows']]; out=[]
        for s in pos+find:
            if s and s not in out:out.append(s)
            if len(out)>=TRACK_LIMIT:break
        return out
    # ========================================================

    # V4.9.0C-4D FULL UNIVERSE Q2 LOGGER

    #

    # Shadow only.

    # Scans DB-resident USA universe once per minute.

    # Does NOT change Heavy Tracker, live ENTRY or Kakao.

    # ========================================================

    def _scan_q2_universe_shadow(

        self,

        db,

        dmap,

        qmap,

        market_context,

    ):

        minute = _now()[:16]



        if getattr(

            self,

            '_q2_universe_scan_minute',

            None

        ) == minute:

            return



        self._q2_universe_scan_minute = minute



        states = getattr(

            self,

            '_q2_universe_states',

            {}

        )



        scanned = 0

        evaluated = 0

        q2_good = 0

        entries = 0

        errors = 0



        for sym, metric in dmap.items():



            sym = str(sym or '').upper()



            if not sym:

                continue



            if not metric.get('daily_history_ok'):

                continue



            scanned += 1



            try:

                ticks = db.ticks(sym, 3000)



                if not ticks:

                    continue



                b1 = ticks_to_bars(ticks, 1)

                b5 = ticks_to_bars(ticks, 5)



                if b1 is None or b5 is None:

                    continue



                if len(b1) < 20 or len(b5) < 4:

                    continue



                shadow = evaluate_rebound(

                    sym,

                    b1,

                    b5,

                    qmap.get(sym) or {},

                    metric,

                    market_context,

                )



                evaluated += 1



                q2_score = _f(

                    shadow.get(

                        'quality2_shadow_score'

                    )

                )



                q2_state = shadow.get(

                    'rebound_state_q2_shadow'

                )



                if q2_score >= 75:

                    q2_good += 1



                prev_state = states.get(sym)



                # First observation only seeds state.

                # No false ENTRY event after service restart.

                if (

                    prev_state is not None

                    and prev_state != q2_state

                    and q2_state == 'REBOUND_ENTRY'

                ):

                    payload = {

                        'market': 'USA',

                        'symbol': sym,

                        'price': shadow.get('price'),

                        'state': q2_state,

                        'quality2_shadow_score': q2_score,

                        'quality2_grade':

                            shadow.get('quality2_grade'),

                        'pullback_score':

                            shadow.get('pullback_score'),

                        'rebound_score':

                            shadow.get('rebound_score'),

                        'risk_pct':

                            shadow.get('risk_pct'),

                        'rebound_shadow': shadow,

                        'source':

                            'Q2_FULL_UNIVERSE_SHADOW',

                    }



                    self.store.event(

                        'USA',

                        sym,

                        'Q2_REBOUND_ENTRY_SHADOW',

                        prev_state,

                        q2_state,

                        power=None,

                        message=(

                            f'{sym} Q2 '

                            f'{prev_state}→{q2_state}'

                        ),

                        payload=payload,

                    )



                    entries += 1



                states[sym] = q2_state



            except Exception:

                errors += 1



        self._q2_universe_states = states



        self._q2_universe_scan_stats = {

            'minute': minute,

            'scanned': scanned,

            'evaluated': evaluated,

            'quality2_75plus': q2_good,

            'new_entries': entries,

            'errors': errors,

        }





    def refresh_usa_tracker(self,db):

        # V4.9.0C-2D LIVE REBOUND SHADOW

        syms = self.tracked_symbols('USA')

        fmap = {

            r['symbol']: r

            for r in self.finder['USA']['rows']

        }

        pmap = {

            p['symbol']: p

            for p in self.store.positions('USA')

        }

        qmap = {

            q.get('symbol'): q

            for q in db.quotes()

        }

    

        daily_rows = db.daily_metrics() or []

        dmap = {

            str(r.get('symbol') or '').upper(): r

            for r in daily_rows

            if r.get('symbol')

        }

    

        spy_metric = dmap.get('SPY') or {}

        qqq_metric = dmap.get('QQQ') or {}

        smh_metric = dmap.get('SMH') or {}

    

        market_context = {

            'spy_return_20d_pct':

                _f(spy_metric.get('return_20d_pct')),

            'qqq_return_20d_pct':

                _f(qqq_metric.get('return_20d_pct')),

            'smh_return_20d_pct':

                _f(smh_metric.get('return_20d_pct')),

        }

    

        self._scan_q2_universe_shadow(

            db,

            dmap,

            qmap,

            market_context,

        )



        rows = []

    

        for sym in syms:

            ticks = db.ticks(sym,40000)
            if ticks:
                _last_et_date = pd.to_datetime(ticks[-1]["ts"], utc=True).tz_convert("America/New_York").date()
                ticks = [t for t in ticks if pd.to_datetime(t["ts"], utc=True).tz_convert("America/New_York").date() == _last_et_date]

            sig = multi_timeframe_signal(

                sym,

                ticks,

                db.quotes(),

            )

    

            rows.append(

                self._usa_row(

                    sym,

                    ticks,

                    sig,

                    qmap,

                    fmap.get(sym),

                    pmap.get(sym),

                    dmap.get(sym) or {},

                    market_context,

                )

            )

    

        rows.sort(key=_tracker_sort_key)

        self._finalize('USA',rows)

        return self.tracker['USA']

    def _usa_row(self,sym,ticks,sig,qmap,finder,pos,metric=None,market_context=None):
        b1=ticks_to_bars(ticks,1); b5=ticks_to_bars(ticks,5); price=_f((qmap.get(sym) or {}).get('price') or (finder or {}).get('price')); sess=_session('USA'); integrity=_data_integrity_usa(price,b1,b5,sess); ind=sig.get('indicators') or {}; vwap=_f(ind.get('vwap')); ema9=_f(ind.get('ema9')); ema20=_f(ind.get('ema20')); rvol=_f(ind.get('rvol')); rsi=_f(ind.get('rsi14'),50)
        # Power V1: 5분 추세를 중심으로 하고 1분은 Trigger/순간 힘으로 사용.
        structure=(8 if price and vwap and price>vwap else -8 if price and vwap else 0)+(8 if ema9 and ema20 and ema9>ema20 else -8 if ema9 and ema20 else 0)
        if len(b5)>=3:
            c0=_f(b5.iloc[-1]['close']); c1=_f(b5.iloc[-2]['close']); c2=_f(b5.iloc[-3]['close'])
            structure+=8 if c0>c1>c2 else -8 if c0<c1<c2 else 3 if c0>c1 else -3
            l0=_f(b5.iloc[-1]['low']); l1=_f(b5.iloc[-2]['low']); h0=_f(b5.iloc[-1]['high']); h1=_f(b5.iloc[-2]['high'])
            if l0>l1 and c0>=c1:structure+=6
            elif h0<h1 and c0<=c1:structure-=6
        vol_ratio=1; micro=0
        if len(b1)>=4:
            vols=pd.to_numeric(b1['volume'],errors='coerce').fillna(0); recent=vols.iloc[-11:-1] if len(vols)>=11 else vols.iloc[:-1]; base=max(_f(recent.median() if len(recent) else 1),1); vol_ratio=_clip(_f(vols.iloc[-1])/base,0,8)
            lo=_f(b1.iloc[-1]['open']); lc=_f(b1.iloc[-1]['close']); micro=1 if lc>lo else -1 if lc<lo else 0
        participation=max(rvol,vol_ratio); volume=micro*_clip(participation-.9,0,3.1)/3.1*25
        momentum=0; rets=[]
        if len(b1)>=6:
            closes=pd.to_numeric(b1['close'],errors='coerce'); now=_f(closes.iloc[-1])
            for n,w in ((1,.35),(3,.40),(5,.25)):
                prev=_f(closes.iloc[-1-n]); ret=(now/prev-1)*100 if prev else 0; rets.append(ret); momentum+=_clip(ret/.8,-1,1)*(20*w)
        qqq=_f((qmap.get('QQQ') or {}).get('change_pct')); spy=_f((qmap.get('SPY') or {}).get('change_pct')); smh=_f((qmap.get('SMH') or {}).get('change_pct')); ref=(.6*qqq+.4*smh) if sym in SEMI else (.6*qqq+.4*spy); market=_clip(ref/1.5,-1,1)*15
        over=((price/vwap-1)*100) if price and vwap else 0; penalty=0; risk='NORMAL'
        if abs(over)>=3.5:penalty+=6; risk='CHASE'
        elif abs(over)>=2.5:penalty+=3; risk='CHASE'
        if rsi>=80 or rsi<=20:penalty+=4; risk='CHASE'
        elif rsi>=74 or rsi<=26:penalty+=2; risk='CHASE'
        if rets and abs(sum(rets))>=3.2:penalty+=4; risk='HIGH'
        raw=structure+volume+momentum+market; power=_clip(raw,-100,100); power=power-penalty if power>0 else power+penalty if power<0 else 0; power=round(power,1)
        prev=self._last.get(('POWER','USA',sym)); delta=round(power-_f(prev.get('power')),1) if prev else 0
        raw_power=power
        if not integrity.get('valid'):power=0.0; delta=0.0
        regular=sess=='REGULAR'
        entry_gate=_usa_entry_trigger(price,vwap,ema9,ema20,rsi,over,vol_ratio,power,delta,b1,b5,risk)
        position_gate=None
        if not integrity.get('valid'):state='DATA_INVALID'
        elif pos:
            position_gate=self._position_manager(power,delta,price,pos,vwap,ema9,ema20,b1,b5); state=position_gate.get('state') or 'HOLD'
        elif not regular:state='WATCH'
        elif entry_gate.get('entry'):state='ENTRY'
        elif entry_gate.get('ready'):state='READY'
        elif entry_gate.get('setup_ok'):state='SETUP'
        else:state='WATCH'
        if pos and position_gate:
            hard=position_gate.get('hard_floor'); warn=position_gate.get('warning_floor'); mode=position_gate.get('floor_mode'); risk_per_share=max(_f(pos.get('avg_entry'))-_f(position_gate.get('initial_floor')),_f(pos.get('avg_entry'))*.004); t1=_f(pos.get('avg_entry'))+2*risk_per_share; t2=_f(pos.get('avg_entry'))+3*risk_per_share
        elif integrity.get('valid') and regular:hard,warn,t1,t2,mode=self._levels(price,pos,power,delta,vwap,ema20,b1,b5)
        else:hard=warn=t1=t2=None; mode='DATA_INVALID' if not integrity.get('valid') else 'REFERENCE_ONLY'
        reason=[]
        if structure>=15:reason.append('가격구조 상승')
        elif structure<=-15:reason.append('가격구조 하락')
        if abs(volume)>=8:reason.append('거래량 유입' if volume>0 else '매도 거래량')
        if abs(momentum)>=7:reason.append('모멘텀 상승' if momentum>0 else '모멘텀 하락')
        if not integrity.get('valid'):
            final_reason='DATA INVALID · '+' / '.join(integrity.get('reasons') or [])
        elif pos and position_gate:
            final_reason=position_gate.get('reason') or state
        elif state=='ENTRY':
            final_reason=f"ENTRY · 5분 Setup {entry_gate['setup_count']}/{entry_gate['setup_total']} · 1분 Trigger {entry_gate['trigger_count']}/{entry_gate['trigger_total']}"
        elif state=='READY':
            final_reason=f"READY · 5분 Setup 완료 · 1분 Trigger {entry_gate['trigger_count']}/{entry_gate['trigger_total']}"
        elif state=='SETUP':
            final_reason=f"SETUP · 5분 조건 {entry_gate['setup_count']}/{entry_gate['setup_total']} · 1분 파동 대기"
        else:
            final_reason=' · '.join(reason[:3]) or '뚜렷한 실시간 힘 없음'
        row = {'market':'USA','symbol':sym,'name':(finder or {}).get('name') or sym,'finder_rank':(finder or {}).get('rank'),'finder_score':(finder or {}).get('finder_score'),'position_open':bool(pos),'qty':_f((pos or {}).get('qty')),'avg_entry':_f((pos or {}).get('avg_entry')),'price':price,'direction':'LONG' if power>=18 else 'SHORT' if power<=-18 else 'NEUTRAL','power':power,'power_delta':delta,'power_label':('강한 상승' if power>=70 else '상승 우세' if power>=30 else '중립' if power>-30 else '하락 우세' if power>-70 else '강한 하락'),'state':state,'risk':risk,'data_integrity':integrity,'entry_gate':entry_gate,'position_gate':position_gate,'raw_power_before_gate':raw_power,'components':{'structure':round(structure,1),'volume':round(volume,1),'momentum':round(momentum,1),'market_sector':round(market,1),'risk_penalty':round(penalty,1),'rvol':round(rvol,2),'volume_ratio':round(vol_ratio,2),'vwap':vwap or None,'ema9':ema9 or None,'ema20':ema20 or None,'rsi':round(rsi,1)},'warning_floor':warn,'hard_floor':hard,'target1':t1,'target2':t2,'floor_mode':mode,'reason':final_reason,'session':sess,'updated_at':_now()}




        # V4.9 POSITION INTELLIGENCE

        # Advisory only · MANUAL ORDER ONLY · NO AUTO ORDER

        try:

            if pos and price and integrity.get('valid'):

                _pcfg = load_portfolio_config()



                row['position_intelligence'] = build_position_intelligence(

                    price=price,

                    entry=_f(pos.get('avg_entry')),

                    qty=_f(pos.get('qty')),

                    power=power,

                    power_delta=delta,

                    entry_power=_f(pos.get('entry_power'), power),

                    peak_power=max(_f(pos.get('entry_power'), power), power),



                    current_floor=_f(pos.get('current_floor')),

                    warning_floor=_f(warn),

                    hard_floor=_f(hard),

                    high_watermark=_f(pos.get('high_watermark'), price),



                    target1=_f(t1),

                    target2=_f(t2),

                    vwap=_f(vwap),



                    total_capital=_pcfg.get('total_capital', 0),

                    available_cash=_pcfg.get('available_cash'),

                    max_position_pct=_pcfg.get('max_position_pct', 15),

                    max_add_pct=_pcfg.get('max_add_pct', 5),

                    risk_per_trade_pct=_pcfg.get('risk_per_trade_pct', 0.75),

                    average_down_enabled=_pcfg.get('average_down_enabled', False),

                )

            else:

                row['position_intelligence'] = {

                    'enabled': False,

                    'action': 'DATA_WAIT',

                    'reason': 'Live data integrity invalid - no trading advice',

                    'guards': {

                        'manual_order_only': True,

                        'auto_order': False

                    }

                }



        except Exception as _pi_err:

            row['position_intelligence'] = {

                'enabled': False,

                'error': str(_pi_err),

                'guards': {

                    'manual_order_only': True,

                    'auto_order': False

                }

            }




        # V4.9.0C-2D LIVE REBOUND SHADOW

        # Shadow only. Existing Power/state/ENTRY remains authoritative.

        try:

            shadow = evaluate_rebound(

                sym,

                b1,

                b5,

                qmap.get(sym) or {},

                metric or {},

                market_context or {},

            )

        

            row['quality_version'] = shadow.get('quality_version')

            row['quality2_ready'] = shadow.get('quality2_ready')

            row['quality2_shadow_score'] = shadow.get(

                'quality2_shadow_score'

            )

            row['quality2_grade'] = shadow.get('quality2_grade')

            row['quality2_watch'] = shadow.get('quality2_watch')

            row['quality2_priority'] = shadow.get(

                'quality2_priority'

            )

            row['quality2_parts'] = shadow.get('quality2_parts')

            row['quality2_flags'] = shadow.get('quality2_flags')

            row['quality2_daily'] = shadow.get('quality2_daily')

        

            row['rebound_state_shadow'] = shadow.get('state')

            row['pullback_score_shadow'] = shadow.get(

                'pullback_score'

            )

            row['rebound_score_shadow'] = shadow.get(

                'rebound_score'

            )

            row['rebound_total_shadow'] = shadow.get(

                'rebound_total'

            )

        

            # Keep complete engine result for Replay/Audit.

            row['rebound_shadow'] = shadow

        

        except Exception as e:

            # Never break the live V4 tracker because Shadow failed.

            row['rebound_shadow_error'] = str(e)[:300]

        

        # V161_FROZEN_CTX_WIRING: replay-equivalent USA paper context only.
        row['williams_frozen_ctx']=self._v161_wire_usa_frozen_ctx(row,b1)
        return row


    def _williams_structure_state(self,b1,entry_price=None,start_time=None):
        """Frozen Williams STRUCT0 live state candidate.

        Causal only:
        - confirm swing-low with 2 bars to the right
        - support may only ratchet upward
        - HOLD while latest close >= confirmed support
        - EXIT_READY when latest close < support
        No RSI/CCI/MACD exit and no re-entry logic.
        """
        out={
            'mode':'STRUCT0_FROZEN_V92',
            'state':'WATCH',
            'support':None,
            'support_updates':0,
            'break':False,
            'entry_price':_f(entry_price) if entry_price else None,
            'last_close':None,
            'bars':0,
        }
        if b1 is None or len(b1)<7:
            return out
        try:
            b=b1.copy().reset_index(drop=True)
            for col in ('open','high','low','close'):
                b[col]=pd.to_numeric(b[col],errors='coerce')

            # V118: only bars strictly after the BUY minute belong to this position.
            # This discards all pre-entry STRUCT0 support and also excludes the partial
            # entry minute. Kiwoom minute timestamps are YYYYMMDDHHMMSS-like strings.
            if start_time and 'time' in b.columns:
                start_digits=''.join(ch for ch in str(start_time) if ch.isdigit())
                if len(start_digits)>=12:
                    b['_v118_time']=b['time'].astype(str).str.replace(r'\D','',regex=True)
                    b=b[b['_v118_time'].str.len()>=12]
                    b=b[b['_v118_time'].str[:12] > start_digits[:12]]
                    b=b.sort_values('_v118_time').reset_index(drop=True)

            b=b.dropna(subset=['high','low','close']).reset_index(drop=True)
            out['bars']=len(b)
            out['structure_start_time']=start_time
            if len(b)<7:return out

            support=None; updates=0
            # A swing at j is only usable when j+2 exists: fully causal.
            for i in range(4,len(b)):
                j=i-2
                lo=_f(b.iloc[j]['low'])
                if lo<=0:continue
                window=[_f(b.iloc[k]['low'],float('inf')) for k in range(j-2,j+3)]
                if lo<=min(window):
                    if support is None:
                        support=lo
                    elif lo>support:
                        support=lo; updates+=1
            last=_f(b.iloc[-1]['close'])
            out['last_close']=last or None
            out['support']=support
            out['support_updates']=updates
            if support is None or last<=0:
                return out
            br=bool(last<support)
            out['break']=br
            out['state']='EXIT_READY' if br else 'HOLD'
            return out
        except Exception as e:
            out['state']='DATA_INVALID'
            out['error']=type(e).__name__
            return out


    def _kr_minute_chart_cached(self,sym,korea,interval=1,cache_seconds=8.0,min_spacing=0.24):
        """Shared KR chart cache + throttle for ka10080.

        Keeps the frozen Williams/shadow logic unchanged while avoiding burst
        duplicate requests that exceed Kiwoom's per-API flow limit.
        """
        import time as _time
        key=(str(sym),int(interval))
        cache=getattr(self,'_kr_chart_cache_v104',None)
        if cache is None:
            cache={}
            self._kr_chart_cache_v104=cache
        now=_time.monotonic()
        hit=cache.get(key)
        if hit and (now-hit[0]) < float(cache_seconds):
            return hit[1]

        last=float(getattr(self,'_kr_chart_last_call_v104',0.0) or 0.0)
        wait=float(min_spacing)-(now-last)
        if wait>0:
            _time.sleep(wait)
        data=korea.minute_chart(sym,int(interval),max_pages=1)
        self._kr_chart_last_call_v104=_time.monotonic()
        cache[key]=(self._kr_chart_last_call_v104,data)
        return data

    def _williams_entry_from_gate(self,sym,gate,finder_rank=None):
        empty={
            'signal':False,
            'stage':'DATA_WAIT',
            'raw_cross':False,
            'trigger':None,
            'rsi2':None,
            'finder_rank':finder_rank,
            'source':'KOREA_SHADOW_GATE_REUSE',
        }
        try:
            raw=(gate or {}).get('williams_signal_bars') or []
            if len(raw)<3:
                return empty
            x=pd.DataFrame(raw)
            need={'time','open','high','low','close'}
            if not need.issubset(set(x.columns)):
                empty['stage']='DATA_INVALID'
                empty['columns']=[str(c) for c in x.columns]
                return empty

            # Kiwoom minute time is normally YYYYMMDDHHMMSS. Keep only valid numeric rows.
            x=x.copy()
            x['time']=x['time'].astype(str).str.replace(r'\\D','',regex=True)
            for c in ('open','high','low','close'):
                x[c]=pd.to_numeric(x[c],errors='coerce').abs()
            x=x.dropna(subset=['open','high','low','close'])
            x=x[x['time'].str.len()>=8]
            if len(x)<3:
                return empty
            x=x.sort_values('time').reset_index(drop=True)
            x['day']=x['time'].str[:8]
            days=[d for d in x['day'].drop_duplicates().tolist() if d]
            if len(days)<2:
                empty['stage']='NEED_PREV_DAY'
                return empty

            cur_day=days[-1]
            prev_day=days[-2]
            prev=x[x['day']==prev_day]
            cur=x[x['day']==cur_day]
            if prev.empty or len(cur)<2:
                return empty

            prev_day_high=float(prev['high'].max())
            prev_day_low=float(prev['low'].min())
            day_open=float(cur.iloc[0]['open'])
            prev_price=float(cur.iloc[-2]['close'])
            current_price=float(cur.iloc[-1]['close'])
            recent_closes=[float(v) for v in cur['close'].tail(30).tolist()]

            # V110: causal same-day recovery for symbols that enter the live candidate pool
            # after the actual Williams CrossUp already happened. Scan only bars that existed
            # at each historical minute, compute RSI2 causally, and recover only a cross that
            # is still inside the original 30-minute confirmation window.
            trigger=day_open + 0.5*(prev_day_high-prev_day_low)
            now_kst=_dt.now(_WILLIAMS_KST)
            st=_WILLIAMS_STATE[(str(sym),now_kst.strftime('%Y%m%d'))]
            recovered_cross_time=None
            recovered_cross_age_min=None
            recovered_cross_rsi2=None

            if not st.get('signal_sent') and st.get('armed_at') is None and len(cur)>=2:
                candidates=[]
                closes_all=[float(v) for v in cur['close'].tolist()]
                for i in range(1,len(cur)):
                    p0=float(cur.iloc[i-1]['close'])
                    p1=float(cur.iloc[i]['close'])
                    if not (p0 <= trigger < p1):
                        continue
                    rsi_i=_williams_rsi2(closes_all[:i+1])
                    if rsi_i is None or rsi_i <= 50.0:
                        continue
                    ts=str(cur.iloc[i]['time'])
                    try:
                        digits=''.join(ch for ch in ts if ch.isdigit())
                        if len(digits)>=14:
                            cross_dt=_dt.strptime(digits[:14],'%Y%m%d%H%M%S').replace(tzinfo=_WILLIAMS_KST)
                        elif len(digits)>=12:
                            cross_dt=_dt.strptime(digits[:12],'%Y%m%d%H%M').replace(tzinfo=_WILLIAMS_KST)
                        else:
                            continue
                    except Exception:
                        continue
                    age=(now_kst-cross_dt).total_seconds()/60.0
                    if 0.0 <= age <= 30.0:
                        candidates.append((cross_dt,age,float(rsi_i)))

                if candidates:
                    cross_dt,age,rsi_i=max(candidates,key=lambda z:z[0])
                    st['armed_at']=cross_dt
                    recovered_cross_time=cross_dt.isoformat()
                    recovered_cross_age_min=round(age,3)
                    recovered_cross_rsi2=round(rsi_i,4)

            out=williams_live_evaluate_v23(
                symbol=sym,
                prev_day_high=prev_day_high,
                prev_day_low=prev_day_low,
                day_open=day_open,
                prev_price=prev_price,
                current_price=current_price,
                recent_closes=recent_closes,
                finder_rank=finder_rank,
                now=now_kst,
            )
            out['source']='KOREA_SHADOW_GATE_REUSE'
            out['historical_cross_recovered']=bool(recovered_cross_time)
            out['recovered_cross_time']=recovered_cross_time
            out['recovered_cross_age_min']=recovered_cross_age_min
            out['recovered_cross_rsi2']=recovered_cross_rsi2

            # V111 STRUCT5 paper-entry trigger.
            # Window = current 1m bar + previous 4 completed 1m bars.
            # Resistance is the highest HIGH of the previous four bars.
            # Entry fires only on a fresh close breakout, RSI2>50, and no lower-low
            # deterioration across the most recent two bars. No future bars are used.
            struct5_signal=False
            struct5_resistance=None
            struct5_higher_low=False
            struct5_rsi2=None
            struct5_reason='NEED_5_BARS'
            if len(cur)>=5:
                w5=cur.tail(5).reset_index(drop=True)
                prev4=w5.iloc[:4]
                nowbar=w5.iloc[4]
                prevbar=w5.iloc[3]
                struct5_resistance=float(prev4['high'].max())
                struct5_rsi2=_williams_rsi2([float(v) for v in cur['close'].tail(30).tolist()])
                # Latest two-bar low structure must not undercut the preceding two-bar low structure.
                old_low=float(w5.iloc[:2]['low'].min())
                new_low=float(w5.iloc[2:4]['low'].min())
                struct5_higher_low=bool(new_low >= old_low)
                fresh_break=bool(float(prevbar['close']) <= struct5_resistance < float(nowbar['close']))
                rank_ok=bool(finder_rank is not None and int(finder_rank) <= 20)
                rsi_ok=bool(struct5_rsi2 is not None and float(struct5_rsi2) > 50.0)
                day_key=now_kst.strftime('%Y%m%d')
                s5=_WILLIAMS_STATE[(str(sym),day_key)]
                already=bool(s5.get('struct5_order_sent'))
                struct5_signal=bool(fresh_break and struct5_higher_low and rank_ok and rsi_ok and not already)
                if struct5_signal:
                    # Detection only. Do NOT consume the signal here.
                    # Broker acknowledgement in _williams_mock_auto_step owns struct5_order_sent.
                    s5['struct5_last_detected_at']=now_kst
                    out['signal']=True
                    out['stage']='ENTRY_CANDIDATE'
                    struct5_reason='FRESH_5BAR_BREAKOUT_PENDING_ORDER'
                elif already:
                    struct5_reason='ORDER_ACKED'
                elif not fresh_break:
                    struct5_reason='WAIT_BREAKOUT'
                elif not struct5_higher_low:
                    struct5_reason='LOW_STRUCTURE_WEAK'
                elif not rank_ok:
                    struct5_reason='RANK_FAIL'
                elif not rsi_ok:
                    struct5_reason='RSI2_FAIL'

            out['struct5_signal']=bool(struct5_signal)
            out['struct5_resistance']=struct5_resistance
            out['struct5_higher_low']=bool(struct5_higher_low)
            out['struct5_rsi2']=struct5_rsi2
            out['struct5_reason']=struct5_reason
            out['prev_day']=prev_day
            out['current_day']=cur_day
            out['prev_day_high']=prev_day_high
            out['prev_day_low']=prev_day_low
            out['day_open']=day_open
            out['prev_price']=prev_price
            out['current_price']=current_price

            # V234_MTF_ENTRY_GUARD: 5m decides direction, 1m decides timing.
            # Applies only to a fresh Williams entry signal; no order side effect here.
            if bool(out.get('signal')):
                try:
                    _m=cur.copy()
                    _m['_dt']=pd.to_datetime(_m['time'].astype(str).str[:14],format='%Y%m%d%H%M%S',errors='coerce')
                    _m=_m.dropna(subset=['_dt']).sort_values('_dt').reset_index(drop=True)
                    _c=pd.to_numeric(_m['close'],errors='coerce').astype(float)
                    _h=pd.to_numeric(_m['high'],errors='coerce').astype(float)
                    _l=pd.to_numeric(_m['low'],errors='coerce').astype(float)

                    def _rsi14(_s):
                        _d=_s.diff(); _g=_d.clip(lower=0); _dn=(-_d.clip(upper=0))
                        _ag=_g.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
                        _ad=_dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
                        _rs=_ag/_ad.replace(0,pd.NA)
                        return (100-(100/(1+_rs))).astype(float)

                    def _macd(_s):
                        _mac=_s.ewm(span=12,adjust=False).mean()-_s.ewm(span=26,adjust=False).mean()
                        _sig=_mac.ewm(span=9,adjust=False).mean(); return _mac,_sig,_mac-_sig

                    def _cci9(_h,_l,_c):
                        _tp=(_h+_l+_c)/3.0; _ma=_tp.rolling(9).mean()
                        _md=_tp.rolling(9).apply(lambda z: float((z-z.mean()).abs().mean()),raw=False)
                        return (_tp-_ma)/(0.015*_md.replace(0,pd.NA))

                    _r1=_rsi14(_c); _mac1,_sig1,_hist1=_macd(_c); _cci1=_cci9(_h,_l,_c)
                    _r1n=float(_r1.iloc[-1]) if len(_r1) and pd.notna(_r1.iloc[-1]) else None
                    _r1p=float(_r1.iloc[-2]) if len(_r1)>1 and pd.notna(_r1.iloc[-2]) else None
                    _h1n=float(_hist1.iloc[-1]) if len(_hist1) and pd.notna(_hist1.iloc[-1]) else None
                    _h1p=float(_hist1.iloc[-2]) if len(_hist1)>1 and pd.notna(_hist1.iloc[-2]) else None
                    _c1n=float(_cci1.iloc[-1]) if len(_cci1) and pd.notna(_cci1.iloc[-1]) else None
                    _c1p=float(_cci1.iloc[-2]) if len(_cci1)>1 and pd.notna(_cci1.iloc[-2]) else None
                    _improve=sum([
                        bool(_r1n is not None and _r1p is not None and _r1n>=_r1p),
                        bool(_h1n is not None and _h1p is not None and _h1n>=_h1p),
                        bool(_c1n is not None and _c1p is not None and _c1n>=_c1p),
                    ])
                    _rsi70_exit=bool(_r1p is not None and _r1n is not None and _r1p>=70.0 and _r1n<70.0)
                    _cci_dump=bool(_c1p is not None and _c1n is not None and ((_c1p-_c1n)>=100.0 or (_c1p>=100.0 and _c1n<100.0)))
                    _one_ok=bool(_improve>=2 and not _rsi70_exit and not _cci_dump)

                    # Build completed 5m candles only; current incomplete 5m bucket is excluded.
                    _m2=_m.set_index('_dt')[['open','high','low','close']].apply(pd.to_numeric,errors='coerce')
                    _bucket=_m['_dt'].iloc[-1].floor('5min')
                    _m2=_m2[_m2.index < _bucket]
                    _b5=_m2.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna().reset_index()
                    _five_ok=False; _r5n=_h5n=_h5p=_ema5=_cl5=None
                    if len(_b5)>=20:
                        _c5=pd.to_numeric(_b5['close'],errors='coerce').astype(float)
                        _r5=_rsi14(_c5); _mac5,_sig5,_hist5=_macd(_c5); _ema20=_c5.ewm(span=20,adjust=False).mean()
                        _r5n=float(_r5.iloc[-1]) if pd.notna(_r5.iloc[-1]) else None
                        _h5n=float(_hist5.iloc[-1]) if pd.notna(_hist5.iloc[-1]) else None
                        _h5p=float(_hist5.iloc[-2]) if len(_hist5)>1 and pd.notna(_hist5.iloc[-2]) else None
                        _m5n=float(_mac5.iloc[-1]); _s5n=float(_sig5.iloc[-1]); _ema5=float(_ema20.iloc[-1]); _cl5=float(_c5.iloc[-1])
                        # V238_MTF_SCORE_GATE: simulation calibration.
                        # 5m is a direction filter, not an exact timing trigger.
                        _five_score=sum([
                            bool(_r5n is not None and _r5n>=45.0),
                            bool((_m5n>=_s5n) or (_h5n is not None and _h5p is not None and _h5n>=_h5p)),
                            bool(_cl5 is not None and _ema5 is not None and _cl5>=_ema5),
                        ])
                        _five_hard_bear=bool(
                            _r5n is not None and _r5n<40.0 and
                            _h5n is not None and _h5p is not None and _h5n<_h5p and
                            _cl5 is not None and _ema5 is not None and _cl5<_ema5
                        )
                        # Strong 1m momentum (3/3 improving) needs only one 5m support.
                        # Normal 1m momentum (2/3) still needs two 5m supports.
                        _five_ok=bool((not _five_hard_bear) and ((_improve>=3 and _five_score>=1) or (_improve>=2 and _five_score>=2)))

                    _mtf_ok=bool(_one_ok and _five_ok)
                    out['v234_mtf_guard']={
                        'ok':_mtf_ok,'one_min_ok':_one_ok,'five_min_ok':_five_ok,'one_improve_count':_improve,
                        'rsi1':_r1n,'rsi1_prev':_r1p,'cci1':_c1n,'cci1_prev':_c1p,'hist1':_h1n,'hist1_prev':_h1p,
                        'rsi70_exit':_rsi70_exit,'cci_dump':_cci_dump,'rsi5':_r5n,'hist5':_h5n,'hist5_prev':_h5p,
                        'close5':_cl5,'ema20_5':_ema5
                    }
                    # V235_MTF_TELEMETRY: record only fresh Williams signals reaching this guard.
                    # No strategy/order authority change.
                    try:
                        _etype='WILLIAMS_MTF_PASS' if _mtf_ok else 'WILLIAMS_MTF_BLOCK'
                        _msg=(f'{sym} MTF ' + ('PASS' if _mtf_ok else 'BLOCK') +
                              f' 1m={_one_ok} 5m={_five_ok} improve={_improve}')
                        self.store.event('KOREA',str(sym),_etype,None,
                                         'ENTRY_CANDIDATE' if _mtf_ok else 'BLOCKED_MTF',
                                         power=None,message=_msg,payload={
                            'price':current_price,'finder_rank':finder_rank,
                            'trigger':out.get('trigger'),'rsi2':out.get('rsi2'),
                            'raw_cross':out.get('raw_cross'),
                            'historical_cross_recovered':out.get('historical_cross_recovered'),
                            'struct5_signal':out.get('struct5_signal'),
                            'struct5_resistance':out.get('struct5_resistance'),
                            'struct5_reason':out.get('struct5_reason'),
                            'mtf':out.get('v234_mtf_guard'),
                        })
                    except Exception:
                        pass
                    if not _mtf_ok:
                        out['signal']=False
                        out['stage']='BLOCKED_MTF'
                    else:
                        # V237_MTF_PASS_BRIDGE_SYNC: make the guarded PASS authoritative
                        # for downstream row/order aliases. Mock-only order path still
                        # applies V233 live-price guard before buy submission.
                        out['signal']=True
                        out['williams_entry']=True
                        out['williams_signal_entry']=True
                        out['stage']='ENTRY_CANDIDATE'
                except Exception as _e:
                    # Fail closed for new mock entries if the MTF guard cannot be evaluated.
                    out['signal']=False
                    out['stage']='BLOCKED_MTF_DATA'
                    out['v234_mtf_guard']={'ok':False,'error':f'{type(_e).__name__}: {_e}'[:180]}

            return out
        except Exception as e:
            empty['stage']='DATA_INVALID'
            empty['error']=f'{type(e).__name__}: {e}'[:240]
            return empty

    def _williams_structure_from_gate(self,gate,entry_price=None,start_time=None):
        """Build frozen STRUCT0 state from already-fetched KR gate bars.

        No API call. No order side effect. Reuses gate['bars_raw'].
        """
        empty={
            'mode':'STRUCT0_FROZEN_V92',
            'state':'DATA_WAIT',
            'support':None,
            'support_updates':0,
            'break':False,
            'entry_price':_f(entry_price) if entry_price else None,
            'last_close':None,
            'bars':0,
            'shadow_only':True,
            'orders_enabled':False,
        }
        try:
            raw=gate.get('bars_raw') if isinstance(gate,dict) else None
            if not raw:
                return empty
            b1=pd.DataFrame(raw)
            if b1.empty:
                return empty
            need={'open','high','low','close'}
            if not need.issubset(set(b1.columns)):
                empty['state']='DATA_INVALID'
                empty['columns']=[str(x) for x in list(b1.columns)[:30]]
                return empty
            out=self._williams_structure_state(b1,entry_price=entry_price,start_time=start_time)
            out['shadow_only']=True
            out['orders_enabled']=False
            out['source']='KOREA_SHADOW_GATE_REUSE'
            return out
        except Exception as e:
            empty['state']='DATA_INVALID'
            empty['error']=type(e).__name__
            empty['error_msg']=str(e)[:200]
            return empty

    def _williams_structure_shadow(self,sym,korea,entry_price=None):
        """Shadow-only adapter for frozen Williams STRUCT0 state.

        Reads KR 1m bars and returns HOLD / EXIT_READY telemetry.
        Never places orders and never changes production state.
        """
        empty={
            'mode':'STRUCT0_FROZEN_V92',
            'state':'DATA_WAIT',
            'support':None,
            'support_updates':0,
            'break':False,
            'entry_price':_f(entry_price) if entry_price else None,
            'last_close':None,
            'bars':0,
            'shadow_only':True,
            'orders_enabled':False,
        }
        try:
            d=self._kr_minute_chart_cached(sym,korea,1)
            if isinstance(d,pd.DataFrame):
                b1=d.copy()
            else:
                raw=d
                if isinstance(d,dict):
                    raw=(d.get('rows') or d.get('data') or d.get('output2') or
                         d.get('output') or d.get('items') or [])
                b1=pd.DataFrame(raw or [])
            if b1.empty:
                return empty

            # Normalize common Kiwoom/engine field names without assuming one response shape.
            aliases={
                'open':['open','stck_oprc','시가'],
                'high':['high','stck_hgpr','고가'],
                'low':['low','stck_lwpr','저가'],
                'close':['close','stck_prpr','현재가','price'],
            }
            ren={}
            for dst,cands in aliases.items():
                if dst in b1.columns: continue
                for c in cands:
                    if c in b1.columns:
                        ren[c]=dst; break
            if ren:b1=b1.rename(columns=ren)
            need={'open','high','low','close'}
            if not need.issubset(set(b1.columns)):
                empty['state']='DATA_INVALID'
                empty['columns']=[str(x) for x in list(b1.columns)[:30]]
                return empty

            # If a time-like column exists, force chronological order.
            for tc in ('datetime','timestamp','ts','time','체결시간','stck_cntg_hour'):
                if tc in b1.columns:
                    try:b1=b1.sort_values(tc).reset_index(drop=True)
                    except Exception:pass
                    break

            out=self._williams_structure_state(b1,entry_price=entry_price)
            out['shadow_only']=True
            out['orders_enabled']=False
            return out
        except Exception as e:
            empty['state']='DATA_INVALID'
            empty['error']=type(e).__name__
            return empty

    def _korea_shadow_gate(self,sym,korea,cache_seconds=45):

        """V4.7.1 observational KR 5m Setup + 1m Trigger gate.



        Shadow only:

        - never changes live direction/state/order behavior

        - Power is attention strength only

        - ka10080 real 1m bars are the source of truth

        """

        now=datetime.now(timezone.utc)

        cached=self._kr_gate_cache.get(sym)



        if cached:

            age=(now-cached['ts']).total_seconds()

            if age<cache_seconds:

                return cached['data']



        empty={

            'shadow_direction':'UNVERIFIED',

            'gate_ready':False,

            'attention_ok':False,

            'setup_side':'NONE',

            'setup_count':0,

            'setup_total':4,

            'trigger_count':0,

            'trigger_total':4,

            'long_setup':False,

            'short_setup':False,

            'long_trigger':False,

            'short_trigger':False,

            'vol_ratio':None,

            'bars_1m':0,

            'bars_5m':0,

            'data_ok':False,

            'error':None

        }



        try:

            d=self._kr_minute_chart_cached(sym,korea,1)

            raw=d.get('bars') or []



            if len(raw)<25:

                out=dict(empty)

                out['bars_1m']=len(raw)

                out['error']='INSUFFICIENT_1M_BARS'

                self._kr_gate_cache[sym]={'ts':now,'data':out}

                return out



            b=pd.DataFrame(raw).copy()



            for col in ('open','high','low','close','volume'):

                b[col]=pd.to_numeric(b[col],errors='coerce')



            b=b.dropna(subset=['open','high','low','close']).copy()



            b['dt']=pd.to_datetime(

                b['time'].astype(str).str[:14],

                format='%Y%m%d%H%M%S',

                errors='coerce'

            )

            b=b.dropna(subset=['dt']).sort_values('dt')

            b=b.drop_duplicates('dt',keep='last').reset_index(drop=True)



            if len(b)<25:

                out=dict(empty)

                out['bars_1m']=len(b)

                out['error']='INSUFFICIENT_NORMALIZED_1M_BARS'

                self._kr_gate_cache[sym]={'ts':now,'data':out}

                return out



            b['ema9']=b['close'].ewm(span=9,adjust=False).mean()

            b['ema20']=b['close'].ewm(span=20,adjust=False).mean()



            med=b['volume'].rolling(20,min_periods=5).median()

            b['vol_ratio']=b['volume']/med.replace(0,pd.NA)



            five=(

                b.set_index('dt')

                 .resample('5min',origin='start_day')

                 .agg({

                     'open':'first',

                     'high':'max',

                     'low':'min',

                     'close':'last',

                     'volume':'sum'

                 })

                 .dropna(subset=['close'])

                 .reset_index()

            )



            if len(five)<4:

                out=dict(empty)

                out['bars_1m']=len(b)

                out['bars_5m']=len(five)

                out['error']='INSUFFICIENT_5M_BARS'

                self._kr_gate_cache[sym]={'ts':now,'data':out}

                return out



            five['ema9']=five['close'].ewm(span=9,adjust=False).mean()

            five['ema20']=five['close'].ewm(span=20,adjust=False).mean()



            a=b.iloc[-1]

            p1=b.iloc[-2]

            f0=five.iloc[-1]

            f1=five.iloc[-2]



            long_setup_checks={

                'close_above_ema9':bool(f0['close']>f0['ema9']),

                'ema9_above_ema20':bool(f0['ema9']>f0['ema20']),

                'close_not_lower':bool(f0['close']>=f1['close']),

                'higher_low_or_break':bool(

                    (f0['low']>=f1['low']) or

                    (f0['close']>f1['high'])

                )

            }



            short_setup_checks={

                'close_below_ema9':bool(f0['close']<f0['ema9']),

                'ema9_below_ema20':bool(f0['ema9']<f0['ema20']),

                'close_not_higher':bool(f0['close']<=f1['close']),

                'lower_high_or_break':bool(

                    (f0['high']<=f1['high']) or

                    (f0['close']<f1['low'])

                )

            }



            long_setup_count=sum(long_setup_checks.values())

            short_setup_count=sum(short_setup_checks.values())

            long_setup=long_setup_count>=3

            short_setup=short_setup_count>=3



            vr=_f(a.get('vol_ratio'),0)

            impulse=((_f(a['close'])/_f(p1['close'])-1)*100) if _f(p1['close']) else 0.0



            long_trigger_checks={

                'green_1m':bool(a['close']>a['open']),

                'break_prev_high':bool(a['close']>p1['high']),

                'volume_expansion':bool(vr>=1.20),

                'one_min_impulse':bool(impulse>=0.10)

            }



            short_trigger_checks={

                'red_1m':bool(a['close']<a['open']),

                'break_prev_low':bool(a['close']<p1['low']),

                'volume_expansion':bool(vr>=1.20),

                'one_min_impulse':bool(impulse<=-0.10)

            }



            long_trigger_count=sum(long_trigger_checks.values())

            short_trigger_count=sum(short_trigger_checks.values())

            # V4.7.2 validated shadow profile:

            # 5m Setup >= 3/4 + 1m Trigger = 4/4.

            long_trigger=long_trigger_count>=4

            short_trigger=short_trigger_count>=4



            shadow='UNVERIFIED'

            setup_side='NONE'

            setup_count=max(long_setup_count,short_setup_count)

            trigger_count=max(long_trigger_count,short_trigger_count)



            if long_setup and long_trigger and not short_setup:

                shadow='LONG'

                setup_side='LONG'

                setup_count=long_setup_count

                trigger_count=long_trigger_count

            elif short_setup and short_trigger and not long_setup:

                shadow='SHORT'

                setup_side='SHORT'

                setup_count=short_setup_count

                trigger_count=short_trigger_count

            elif long_setup:

                setup_side='LONG'

                setup_count=long_setup_count

                trigger_count=long_trigger_count

            elif short_setup:

                setup_side='SHORT'

                setup_count=short_setup_count

                trigger_count=short_trigger_count



            out={

                'shadow_direction':shadow,

                'gate_ready':False,  # set by tracker after attention-power check

                'attention_ok':False,

                'setup_side':setup_side,

                'setup_count':setup_count,

                'setup_total':4,

                'trigger_count':trigger_count,

                'trigger_total':4,

                'long_setup':long_setup,

                'short_setup':short_setup,

                'long_trigger':long_trigger,

                'short_trigger':short_trigger,

                'long_setup_count':long_setup_count,

                'short_setup_count':short_setup_count,

                'long_trigger_count':long_trigger_count,

                'short_trigger_count':short_trigger_count,

                'vol_ratio':round(vr,2),

                'impulse_1m_pct':round(impulse,3),

                'bars_1m':len(b),

                'bars_5m':len(five),

                'latest_1m':str(a.get('time')),

                'latest_price':_f(a.get('close')),

                # V125: preserve time so V118 can filter strictly post-entry bars.
                'bars_raw':b[[c for c in ('time','open','high','low','close') if c in b.columns]].tail(240).to_dict('records'),

                'williams_signal_bars':b[[c for c in ('time','open','high','low','close') if c in b.columns]].tail(900).to_dict('records'),

                'latest_5m':five.iloc[-1]['dt'].isoformat(),

                'data_ok':True,

                'error':None,

                'long_setup_checks':long_setup_checks,

                'short_setup_checks':short_setup_checks,

                'long_trigger_checks':long_trigger_checks,

                'short_trigger_checks':short_trigger_checks

            }



        except Exception as e:

            out=dict(empty)

            out['error']=str(e)[:300]



        self._kr_gate_cache[sym]={'ts':now,'data':out}

        return out



    def refresh_korea_tracker(self,korea):

        # V108: separate Williams ENTRY discovery from legacy/open-position tracking.
        # Paper-entry candidates must not be displaced by existing portfolio positions.
        finder_rows=list(self.finder['KOREA'].get('rows') or [])
        fmap={str(r.get('symbol') or ''):r for r in finder_rows if str(r.get('symbol') or '')}

        pmap={p['symbol']:p for p in self.store.positions('KOREA')}

        pulse_rows=list(korea.intraday_pulse.get('rows') or [])
        pulse={
            str(r.get('symbol') or ''):r
            for r in pulse_rows
            if str(r.get('symbol') or '')
        }

        # Candidate pool: Finder rank first, then live pulse strength/score.
        # Explicitly exclude legacy/open positions so Williams ENTRY scans fresh symbols.
        pos_syms=set(str(x) for x in pmap.keys())
        candidate_syms=[]

        for r in sorted(
            finder_rows,
            key=lambda x:(
                int(x.get('rank')) if str(x.get('rank') or '').isdigit() else 999999,
                -_f(x.get('finder_score'))
            )
        ):
            sym=str(r.get('symbol') or '')
            if sym and sym not in pos_syms and sym not in candidate_syms:
                candidate_syms.append(sym)

        for r in sorted(
            pulse_rows,
            key=lambda x:max(
                _f(x.get('live_score')),
                _f(x.get('strength_composite')),
                _f(x.get('change_pct')),
                _f(x.get('rate'))
            ),
            reverse=True
        ):
            sym=str(r.get('symbol') or '')
            if sym and sym not in pos_syms and sym not in candidate_syms:
                candidate_syms.append(sym)

        # V120: holdings FIRST so _finalize rows[:TRACK_LIMIT] cannot drop them.
        # Current mock max positions is 5, matching the final tracker safety window.
        _held_syms=self._williams_mock_held_symbols()
        syms=list(_held_syms)
        for _cand_sym in candidate_syms:
            if _cand_sym not in syms:
                syms.append(_cand_sym)
            if len(syms)>=8:
                break

        # Paper-validation attention rank. Finder rank remains authoritative when present;
        # otherwise use the order of the bounded live candidate pool (1..8).
        pulse_candidate_rank={sym:i+1 for i,sym in enumerate(syms)}

        rows=[]



        for sym in syms:

            f=fmap.get(sym) or {}

            p=pulse.get(sym) or {}



            strength=p.get('strength_composite')

            score=_f(p.get('live_score',f.get('finder_score')))

            bias=str(

                p.get('bias') or

                f.get('direction') or

                'NEUTRAL'

            ).upper()



            sc=(

                _clip((_f(strength)-100)/35,-1,1)*45

                if strength is not None else 0

            )

            ss=_clip((score-50)/50,-1,1)*40



            sign=(

                1 if bias in ('LONG','UP')

                else -1 if bias in ('SHORT','DOWN')

                else 0

            )



            power=round(_clip(sign*abs(ss)+sc,-100,100),1)



            prev=self._last.get(('POWER','KOREA',sym))

            delta=round(

                power-_f(prev.get('power')),1

            ) if prev else 0



            vi=bool(p.get('vi_triggered'))

            risk='HIGH' if vi else str(f.get('risk') or 'NORMAL')



            # V4.7.1 Shadow Gate telemetry.

            # Still no live KR directional state.

            gate=self._korea_shadow_gate(sym,korea)

            # WILLIAMS STRUCT0 V94 SHADOW ONLY: no state/order authority.
            _wpos=pmap.get(sym) or {}
            _wentry=_f(_wpos.get('avg_entry')) or None
            _wmock_st=self._last.get(("WILLIAMS_MOCK",str(sym).zfill(6)),{})
            _wmock_start=(
                _wmock_st.get("entered_bar_time")
                if isinstance(_wmock_st,dict) and _wmock_st.get("in_pos")
                else None
            )
            williams_struct=self._williams_structure_from_gate(
                gate,entry_price=_wentry,start_time=_wmock_start
            )
            _finder_rank=f.get('rank')
            _williams_rank=_finder_rank if _finder_rank is not None else pulse_candidate_rank.get(sym)
            _williams_rank_source='FINDER' if _finder_rank is not None else 'LIVE_CANDIDATE_POOL'
            williams_entry_eval=self._williams_entry_from_gate(sym,gate,finder_rank=_williams_rank)



            # V4.7.2: Attention Power is diagnostic only.

            # It no longer gates directional Shadow READY.

            attention_ok=True

            shadow_direction=gate.get('shadow_direction','UNVERIFIED')

            gate_ready=bool(

                shadow_direction in ('LONG','SHORT') and

                gate.get('data_ok')

            )



            gate['attention_ok']=attention_ok

            gate['attention_filter_required']=False

            gate['gate_ready']=gate_ready



            # V4.6.6 Direction Guard remains authoritative.

            state='HOLD' if pmap.get(sym) else 'WATCH'



            ap=abs(power)

            attention_label=(

                '매우 강한 관심도' if ap>=70 else

                '강한 관심도' if ap>=40 else

                '보통 관심도' if ap>=18 else

                '낮은 관심도'

            )



            reason='KR 5m Setup≥3/4 + 1m Trigger=4/4 Shadow Gate · 라이브 방향 미검증'



            if gate.get('data_ok'):

                reason+=(

                    f" · Shadow {shadow_direction}"

                    f" · 5m {gate.get('setup_count')}/4"

                    f" · 1m {gate.get('trigger_count')}/4"

                )



                if gate_ready:

                    reason+=' · SHADOW READY'

            elif gate.get('error'):

                reason+=f" · Gate data {gate.get('error')}"




            # KR_SHADOW_PROTO_V2
            # Prototype-only decision layer. Production state/direction stay unchanged.
            proto_action='WATCH'
            proto_reason='Shadow Gate 조건 대기'
            data_ok=bool(gate.get('data_ok'))
            setup_n=int(_f(gate.get('setup_count')))
            trigger_n=int(_f(gate.get('trigger_count')))
            proto_conf=round(_clip(
                (setup_n/4.0)*35 +
                (trigger_n/4.0)*45 +
                min(abs(power),100)/100.0*20,
                0,100
            ),1)

            has_pos=bool(pmap.get(sym))
            if not data_ok:
                proto_action='DATA_WAIT'
                proto_reason='1분/5분 Shadow Gate 데이터 준비 중'
            elif has_pos:
                if gate_ready and shadow_direction=='SHORT':
                    proto_action='EXIT_REVIEW'
                    proto_reason='보유중 + SHORT Shadow Gate READY · 수동 청산 검토'
                elif gate_ready and shadow_direction=='LONG' and power>=40 and delta>=0:
                    proto_action='ADD_REVIEW'
                    proto_reason='보유중 + LONG Gate READY + Power 유지/상승 · 추매 검토'
                elif shadow_direction=='LONG':
                    proto_action='HOLD'
                    proto_reason='LONG 구조 유지 · 보유 관찰'
                else:
                    proto_action='HOLD_WATCH'
                    proto_reason='보유중 · 방향 확정 전 관찰'
            else:
                if gate_ready and shadow_direction=='LONG':
                    proto_action='BUY_REVIEW'
                    proto_reason='LONG Shadow Gate READY · 수동 진입 검토'
                elif gate_ready and shadow_direction=='SHORT':
                    proto_action='AVOID'
                    proto_reason='SHORT Shadow Gate READY · 신규매수 회피'
                else:
                    proto_action='WATCH'
                    proto_reason='Setup/Trigger 추가 확인 대기'

            rows.append({

                'market':'KOREA',

                'symbol':sym,

                'williams_candidate':True,
                'tracker_role':'WILLIAMS_ENTRY_CANDIDATE',

                'name':f.get('name') or sym,

                'finder_rank':_williams_rank,
                'williams_rank_source':_williams_rank_source,

                'finder_score':f.get('finder_score'),



                'position_open':bool(pmap.get(sym)),

                'qty':_f((pmap.get(sym) or {}).get('qty')),

                'avg_entry':_f((pmap.get(sym) or {}).get('avg_entry')),

                'price':(_f(p.get('price')) or _f(f.get('price')) or _f(gate.get('latest_price'))),



                # Live direction remains blocked.

                'direction':'UNVERIFIED',



                'power':power,

                'power_delta':delta,

                'power_label':attention_label,



                'state':state,

                'risk':risk,




                'prototype_engine':'KR_SHADOW_PROTO_V2',
                'prototype_action':proto_action,
                'prototype_confidence':proto_conf,
                'prototype_reason':proto_reason,

                # Williams STRUCT0 shadow telemetry (diagnostic only).
                'williams_entry':bool(williams_entry_eval.get('signal')),
                'williams_signal_entry':bool(williams_entry_eval.get('signal')),
                'williams_entry_stage':williams_entry_eval.get('stage'),
                'williams_entry_trigger':williams_entry_eval.get('trigger'),
                'williams_entry_rsi2':williams_entry_eval.get('rsi2'),
                'williams_entry_raw_cross':bool(williams_entry_eval.get('raw_cross')),
                'williams_cross_recovered':bool(williams_entry_eval.get('historical_cross_recovered')),
                'williams_cross_time':williams_entry_eval.get('recovered_cross_time'),
                'williams_cross_age_min':williams_entry_eval.get('recovered_cross_age_min'),
                'williams_struct5_signal':bool(williams_entry_eval.get('struct5_signal')),
                'williams_struct5_resistance':williams_entry_eval.get('struct5_resistance'),
                'williams_struct5_higher_low':bool(williams_entry_eval.get('struct5_higher_low')),
                'williams_struct5_reason':williams_entry_eval.get('struct5_reason'),
                'williams_struct5_order_acked':bool(_WILLIAMS_STATE[(str(sym),_dt.now(_WILLIAMS_KST).strftime('%Y%m%d'))].get('struct5_order_sent')),
                'williams_entry_eval':williams_entry_eval,
                'williams_struct_state':williams_struct.get('state'),
                'williams_support':williams_struct.get('support'),
                'williams_support_updates':williams_struct.get('support_updates'),
                'williams_exit_ready':bool(williams_struct.get('break')),
                'williams_structure_shadow':williams_struct,

                'components':{

                    'execution_strength':strength,

                    'live_score':score,

                    'attention_power':power,

                    'legacy_bias':bias,



                    'minute_chart_gate':True,

                    'direction_verified':False,



                    'shadow_direction':shadow_direction,

                    'shadow_gate_ready':gate_ready,

                    'shadow_setup_side':gate.get('setup_side'),

                    'shadow_setup_count':gate.get('setup_count'),

                    'shadow_setup_total':gate.get('setup_total'),

                    'shadow_trigger_count':gate.get('trigger_count'),

                    'shadow_trigger_total':gate.get('trigger_total'),

                    'shadow_attention_ok':attention_ok,
                    'shadow_attention_filter_required':False,

                    'shadow_data_ok':gate.get('data_ok'),

                    'shadow_vol_ratio':gate.get('vol_ratio'),

                    'shadow_impulse_1m_pct':gate.get('impulse_1m_pct'),

                    'shadow_gate_error':gate.get('error')

                },



                # Full diagnostic is retained in feature_json snapshot.

                'shadow_gate':gate,



                'warning_floor':None,

                'hard_floor':None,

                'target1':None,

                'target2':None,

                'floor_mode':'PENDING',



                'reason':reason,

                'session':_session('KOREA'),

                'updated_at':_now()

            })



        rows.sort(key=_tracker_sort_key)

        self._finalize('KOREA',rows)

        return self.tracker['KOREA']



    def _position_manager(self,power,delta,price,pos,vwap,ema9,ema20,b1,b5):
        entry=_f(pos.get('avg_entry'))
        if not entry or not price:return {'state':'HOLD','floor_mode':'INITIAL','reason':'포지션 데이터 부족'}
        rp=.35
        if len(b1)>=8:
            x=b1.tail(20); rng=((pd.to_numeric(x['high'])-pd.to_numeric(x['low']))/pd.to_numeric(x['close']).replace(0,pd.NA)*100).dropna()
            if len(rng):rp=max(.15,min(2.5,_f(rng.median(),.35)))
        buf_pct=max(.15,.65*rp); buf_abs=entry*buf_pct/100
        recent1=_f(pd.to_numeric(b1.tail(6)['low'],errors='coerce').min()) if len(b1)>=2 else 0
        recent5=_f(pd.to_numeric(b5.tail(4)['low'],errors='coerce').min()) if len(b5)>=2 else 0
        supports=[x for x in (recent5,ema20,vwap) if x and x<entry]; structural=max(supports) if supports else entry-2*buf_abs
        initial=_f(pos.get('initial_floor'))
        if not initial:initial=max(min(entry-entry*.004,structural-buf_abs),entry-entry*.04)
        high=max(_f(pos.get('high_watermark'),entry),price); R=max(entry-initial,entry*.004); profit_r=(price-entry)/R; pnl=(price/entry-1)*100
        prev_floor=_f(pos.get('current_floor'),initial); mode='INITIAL'; floor=initial
        if profit_r>=.8:mode='PROTECT'; floor=max(floor,entry-.10*R)
        if profit_r>=1.5:
            mode='TRAILING'; refs=[]
            if recent1 and recent1<price:refs.append(recent1-buf_abs*.35)
            if recent5 and recent5<price:refs.append(recent5-buf_abs*.25)
            if ema9 and ema9<price:refs.append(ema9-buf_abs*.25)
            if refs:floor=max(floor,max(refs))
        floor=max(prev_floor,floor); floor=min(floor,price-buf_abs*.15); warning=floor+.25*R
        one_break=bool(len(b1)>=3 and _f(b1.iloc[-1]['close'])<_f(b1.iloc[-2]['low']))
        five_break=False; five_rising=False
        if len(b5)>=3:
            c0=_f(b5.iloc[-1]['close']); c1=_f(b5.iloc[-2]['close']); l0=_f(b5.iloc[-1]['low']); l1=_f(b5.iloc[-2]['low'])
            five_break=bool(c0<c1 and l0<l1); five_rising=bool(c0>=c1 and l0>=l1)
        below_vwap=bool(vwap and price<vwap); ema_bear=bool(ema9 and ema20 and ema9<ema20); partial_done=bool(_f(pos.get('partial_exit_done')))
        state='HOLD'; reason='5분 추세/포지션 구조 유지'; pct=0
        if price<=floor:state='HARD_EXIT'; reason='Hard Floor 이탈'; pct=100
        elif five_break and below_vwap and power<=10:state='HARD_EXIT'; reason='5분 구조 붕괴 + VWAP 이탈 + Power 약화'; pct=100
        elif (five_break and (below_vwap or ema_bear)) or (power<20 and delta<=-12):state='EXIT_READY'; reason='5분 구조/추세 약화 · 잔여물량 정리 준비'; pct=100
        elif not partial_done and profit_r>=.8 and five_rising and (one_break or delta<=-12 or power<35):state='PARTIAL_EXIT'; reason='수익구간에서 1분 힘 약화 · 30~50% 축소 검토'; pct=50
        self.store.update_position_risk('USA',pos.get('symbol') or '',initial,floor,warning,high,mode,entry_power=pos.get('entry_power') if pos.get('entry_power') is not None else power,partial_exit_done=partial_done)
        return {'state':state,'reason':reason,'suggested_exit_pct':pct,'initial_floor':round(initial,4),'hard_floor':round(floor,4),'warning_floor':round(warning,4),'floor_mode':mode,'high_watermark':round(high,4),'R':round(R,4),'profit_r':round(profit_r,3),'pnl_pct':round(pnl,3),'one_min_break':one_break,'five_min_break':five_break,'below_vwap':below_vwap,'ema_bear':ema_bear}

    def _levels(self,price,pos,power,delta,vwap,ema20,b1,b5):
        if not price:return None,None,None,None,'NORMAL'
        rp=.35
        if len(b1)>=8:
            x=b1.tail(20); rng=((pd.to_numeric(x['high'])-pd.to_numeric(x['low']))/pd.to_numeric(x['close']).replace(0,pd.NA)*100).dropna(); rp=max(.15,min(2.5,_f(rng.median(),.35))) if len(rng) else rp
        buf=max(.15,.65*rp); support=[]
        if vwap and vwap<price:support.append(vwap)
        if ema20 and ema20<price:support.append(ema20)
        if len(b5)>=3:
            low=_f(pd.to_numeric(b5.tail(4)['low'],errors='coerce').min());
            if low and low<price:support.append(low)
        structural=max(support) if support else price*(1-buf/100*2); hard=structural*(1-buf/100); warn=max(structural,hard*(1+buf/100)); mode='NORMAL'; entry=_f((pos or {}).get('avg_entry'))
        if pos and entry:
            pnl=(price/entry-1)*100
            if pnl>=2 or power<45 or delta<=-12: mode='TIGHT'; warn=max(warn,price*(1-max(.2,buf*.8)/100)); hard=max(hard,price*(1-max(.35,buf*1.2)/100))
            elif abs(power)>=75 and delta>=0:mode='WIDE'
            risk=max(entry-hard,entry*.004); t1=entry+2*risk; t2=entry+3*risk
        else:
            risk=max(price-hard,price*.004); t1=price+2*risk; t2=price+3*risk
        return round(hard,4),round(warn,4),round(t1,4),round(t2,4),mode


    def _v141_build_usa_frozen_ctx(self, row, gate=None, b1=None):
        """Construct frozen Williams USA context from causal live data only."""
        try:
            if str((row or {}).get('market','')).upper()!='USA': return None
            g=gate or {}
            bars=b1
            if bars is None or len(bars)<21: return {'ready':False,'reason':'BARS_LT_21'}
            closes=[_f(v) for v in bars['close'].tolist()]
            highs=[_f(v) for v in bars['high'].tolist()]
            lows=[_f(v) for v in bars['low'].tolist()]
            vols=[_f(v) for v in bars['volume'].tolist()] if 'volume' in bars.columns else [0.0]*len(bars)
            if not closes or min(len(closes),len(highs),len(lows))<21: return {'ready':False,'reason':'BARS_INVALID'}
            i=len(closes)-1
            # Prefer already-computed causal Williams diagnostics from gate/row. V141 does not invent strategy logic.
            ctx={
                'ts': (bars.iloc[-1].get('time') if 'time' in bars.columns else (bars.iloc[-1].get('ts') if 'ts' in bars.columns else None)),
                'prev_crossed': bool((row or {}).get('williams_cross_seen') or g.get('williams_cross_seen')),
                'cross_now': bool((row or {}).get('williams_entry_raw_cross') or g.get('williams_raw_cross')),
                'rsi2': (row or {}).get('williams_entry_rsi2'),
                'day_open': g.get('williams_day_open') or (row or {}).get('williams_day_open'),
                'prev_high': g.get('williams_prev_high') or (row or {}).get('williams_prev_high'),
                'prev_low': g.get('williams_prev_low') or (row or {}).get('williams_prev_low'),
                'volume': vols[i],
                'prior10_volume_avg': (sum(vols[max(0,i-10):i])/len(vols[max(0,i-10):i])) if i>0 and vols[max(0,i-10):i] else 0.0,
                'cci20': g.get('williams_cci20') or (row or {}).get('williams_cci20'),
                'macd_hist': g.get('williams_macd_hist') or (row or {}).get('williams_macd_hist'),
                'prev_macd_hist': g.get('williams_prev_macd_hist') or (row or {}).get('williams_prev_macd_hist'),
            }
            missing=[k for k,v in ctx.items() if v is None]
            out={'ready':not missing,'missing':missing,'entry_args':ctx if not missing else None}
            pos=(row or {}).get('position') or {}
            exit_args={
                'entry_price': (row or {}).get('avg_entry') or pos.get('avg_entry'),
                'price': (row or {}).get('price'),
                'macd': g.get('macd') or (row or {}).get('macd'),
                'signal': g.get('macd_signal') or (row or {}).get('macd_signal'),
                'cci20': ctx.get('cci20'),
                'prev_cci20': g.get('williams_prev_cci20') or (row or {}).get('williams_prev_cci20'),
                'weak_run': (row or {}).get('williams_combo_weak_run') or 0,
            }
            if exit_args['entry_price'] and all(exit_args.get(k) is not None for k in ('price','macd','signal','cci20','prev_cci20')):
                out['exit_args']=exit_args
            else:
                out['exit_args']=None
            return out
        except Exception as e:
            return {'ready':False,'reason':'ERROR','error':str(e)}


    def _v142_build_usa_frozen_ctx(self, row, b1, prev_day=None):
        """Build frozen USA Williams context. USA only; no order authority."""
        try:
            if str((row or {}).get('market','')).upper()!='USA' or b1 is None or len(b1)<25:
                return None
            # Required columns must exist on live 1m bars.
            need=('time','open','high','low','close','volume')
            if any(c not in b1.columns for c in need):
                return None
            closes=[_f(v) for v in b1['close'].tolist()]
            highs=[_f(v) for v in b1['high'].tolist()]
            lows=[_f(v) for v in b1['low'].tolist()]
            vols=[_f(v) for v in b1['volume'].tolist()]
            if len(closes)<21:return None
            # Reuse engine's frozen Williams helpers where available.
            rsi2=_williams_rsi2(closes[-60:])
            # CCI20 causal on completed/current 1m bars.
            tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
            cci20=None
            if len(tp)>=20:
                w=tp[-20:]; m=sum(w)/20.0; md=sum(abs(x-m) for x in w)/20.0
                cci20=0.0 if md==0 else (tp[-1]-m)/(0.015*md)
            # EMA12/26 + signal9, same recurrence as replay.
            def _ema(vals,span):
                if not vals:return []
                a=2.0/(span+1.0);o=[float(vals[0])]
                for v in vals[1:]:o.append(a*float(v)+(1-a)*o[-1])
                return o
            e12=_ema(closes,12);e26=_ema(closes,26);mac=[a-b for a,b in zip(e12,e26)];sg=_ema(mac,9)
            hist=[a-b for a,b in zip(mac,sg)]
            if len(hist)<2 or rsi2 is None or cci20 is None:return None
            prior=vols[-11:-1]
            va=(sum(prior)/len(prior)) if prior else 0.0
            cur=closes[-1]; prv=closes[-2]
            # prev-day OHLC must be supplied by row/gate context; do not invent it.
            day_open=_f((row or {}).get('day_open') or (row or {}).get('session_open'))
            ph=_f((row or {}).get('prev_day_high'))
            pl=_f((row or {}).get('prev_day_low'))
            if not (day_open and ph and pl):return None
            trigger=day_open+0.5*(ph-pl)
            cross_now=bool(prv<=trigger<cur)
            prev_crossed=bool((row or {}).get('williams_frozen_cross_seen'))
            ts=b1.iloc[-1]['time']
            return {
                'entry_args':{
                    'ts':ts,'prev_crossed':prev_crossed,'cross_now':cross_now,'rsi2':rsi2,
                    'day_open':day_open,'prev_high':ph,'prev_low':pl,'volume':vols[-1],
                    'prior10_volume_avg':va,'cci20':cci20,'macd_hist':hist[-1],
                    'prev_macd_hist':hist[-2],
                },
                'feature_snapshot':{'rsi2':rsi2,'cci20':cci20,'macd_hist':hist[-1],
                                    'prev_macd_hist':hist[-2],'volume_ratio':(vols[-1]/va if va else 0.0),
                                    'trigger':trigger,'cross_now':cross_now}
            }
        except Exception as e:
            return {'error':str(e)}


    def _v161_wire_usa_frozen_ctx(self, row, b1):
        """Build replay-equivalent frozen USA entry+exit context from live 1m bars."""
        try:
            if str((row or {}).get('market','')).upper()!='USA' or b1 is None or len(b1)<25:
                return None
            need=('time','open','high','low','close','volume')
            if any(c not in b1.columns for c in need):
                return None
            x=b1.copy().reset_index(drop=True)
            # Parse timestamps. Aware timestamps are converted to ET; naive timestamps are
            # treated as already ET-local, matching the frozen replay wall-clock semantics.
            ts=pd.to_datetime(x['time'],errors='coerce')
            if ts.isna().all(): return None
            try:
                if getattr(ts.dt,'tz',None) is not None:
                    et=ts.dt.tz_convert('America/New_York')
                else:
                    et=ts.dt.tz_localize('America/New_York',ambiguous='NaT',nonexistent='shift_forward')
            except Exception:
                et=ts
            mins=et.dt.hour*60+et.dt.minute
            dates=et.dt.strftime('%Y-%m-%d')
            reg=(mins>=570)&(mins<960)
            if not bool(reg.any()): return None
            current_date=str(dates.iloc[-1])
            cur_idx=x.index[reg & (dates==current_date)].tolist()
            if not cur_idx:
                # Premarket: current regular-session open does not yet exist. No fake context.
                return None
            prior_dates=sorted(set(str(d) for d in dates[reg & (dates<current_date)].dropna().tolist()))
            if not prior_dates:return None
            prev_date=prior_dates[-1]
            prev_idx=x.index[reg & (dates==prev_date)].tolist()
            if not prev_idx:return None
            day_open=_f(x.loc[cur_idx[0],'open'])
            prev_high=max(_f(x.loc[i,'high']) for i in prev_idx)
            prev_low=min(_f(x.loc[i,'low']) for i in prev_idx)
            if not (day_open and prev_high and prev_low):return None

            closes=[_f(v) for v in x['close'].tolist()]
            highs=[_f(v) for v in x['high'].tolist()]
            lows=[_f(v) for v in x['low'].tolist()]
            vols=[_f(v) for v in x['volume'].tolist()]
            if len(closes)<21:return None
            rsi2=_williams_rsi2(closes[-60:])
            tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
            def _cci_at(end):
                if end<19:return None
                w=tp[end-19:end+1]; m=sum(w)/20.0; md=sum(abs(v-m) for v in w)/20.0
                return 0.0 if md==0 else (tp[end]-m)/(0.015*md)
            cci20=_cci_at(len(tp)-1); prev_cci20=_cci_at(len(tp)-2)
            if rsi2 is None or cci20 is None or prev_cci20 is None:return None
            def _ema(vals,span):
                if not vals:return []
                a=2.0/(span+1.0); out=[float(vals[0])]
                for v in vals[1:]:out.append(a*float(v)+(1-a)*out[-1])
                return out
            e12=_ema(closes,12); e26=_ema(closes,26); mac=[a-b for a,b in zip(e12,e26)]; sig=_ema(mac,9)
            hist=[a-b for a,b in zip(mac,sig)]
            if len(hist)<2:return None
            prior=vols[-11:-1]; va=(sum(prior)/len(prior)) if prior else 0.0
            trigger=day_open+0.5*(prev_high-prev_low)
            prv=closes[-2]; cur=closes[-1]; cross_now=bool(prv<=trigger<cur)
            sym=str((row or {}).get('symbol') or '').upper()
            day=current_date.replace('-','')
            cross_key=('WUF_CROSS',sym,day)
            cross_state=self._last.get(cross_key,{}) if sym else {}
            prev_crossed=bool((cross_state or {}).get('seen'))
            entry_args={
                'ts':x.iloc[-1]['time'],'prev_crossed':prev_crossed,'cross_now':cross_now,
                'rsi2':rsi2,'day_open':day_open,'prev_high':prev_high,'prev_low':prev_low,
                'volume':vols[-1],'prior10_volume_avg':va,'cci20':cci20,
                'macd_hist':hist[-1],'prev_macd_hist':hist[-2],
            }
            # Mark first cross after capturing prev_crossed=False for this exact evaluation.
            if sym and cross_now:self._last[cross_key]={'seen':True}
            out={'entry_args':entry_args,'feature_snapshot':{
                'day_open':day_open,'prev_high':prev_high,'prev_low':prev_low,
                'rsi2':rsi2,'cci20':cci20,'prev_cci20':prev_cci20,
                'macd':mac[-1],'signal':sig[-1],'macd_hist':hist[-1],
                'prev_macd_hist':hist[-2],'volume_ratio':(vols[-1]/va if va else 0.0),
                'trigger':trigger,'cross_now':cross_now,'prev_crossed':prev_crossed,
                'trade_date':current_date}}
            try:
                ppos=self.paper.position('USA',sym) if sym and hasattr(self.paper,'position') else None
            except Exception:
                ppos=None
            if not ppos:
                if sym:self._last[('WUF_WEAK',sym)]={'run':0}
                out['exit_args']=None
                return out
            ep=_f((ppos or {}).get('avg_entry') or (ppos or {}).get('entry_price') or (ppos or {}).get('price'))
            weak_state=self._last.get(('WUF_WEAK',sym),{}) if sym else {}
            if ep:
                out['exit_args']={
                    'entry_price':ep,'price':_f((row or {}).get('price') or closes[-1]),
                    'macd':mac[-1],'signal':sig[-1],'cci20':cci20,'prev_cci20':prev_cci20,
                    'prev_macd':mac[-2] if len(mac)>=2 else None,
                    'prev_signal':sig[-2] if len(sig)>=2 else None,
                    'weak_run':int((weak_state or {}).get('run') or 0),
                }
            else:
                out['exit_args']=None
            return out
        except Exception as e:
            return {'error':str(e)}

    def _v140_usa_frozen_williams_eval(self, row):
        """USA-only frozen Williams paper evaluator. No broker authority."""
        if _wuf is None or str((row or {}).get('market','')).upper()!='USA':
            return {'entry':False,'exit':False,'reason':'NOT_USA_OR_MODULE_MISSING'}
        try:
            ctx=(row or {}).get('williams_frozen_ctx') or {}
            out={'entry':False,'exit':False,'reason':'NO_CTX'}
            if ctx.get('entry_args'):
                e=_wuf.entry_signal(**ctx['entry_args'])
                out.update({'entry':bool(e.get('signal')),'entry_eval':e,'reason':'ENTRY_EVAL'})
            if ctx.get('exit_args'):
                x=_wuf.exit_signal(**ctx['exit_args'])
                out.update({'exit':bool(x.get('exit')),'exit_eval':x,'reason':'EXIT_EVAL'})
            return out
        except Exception as e:
            return {'entry':False,'exit':False,'reason':'ERROR','error':str(e)}

    def _usa_mock_order_step(self, action, row):

        """V253: mirror USA frozen Williams decisions to Kiwoom US mock only."""

        import os, logging

        flag=(os.getenv("WILLIAMS_KIWOOM_US_MOCK_AUTO") or "0").lower()

        if flag not in ("1","true","yes","on"):

            return None



        from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker



        sym=str((row or {}).get("symbol") or "").upper().strip()

        if not sym:

            return None



        price=_f((row or {}).get("price"))

        if price <= 0:

            return None



        ex=str((row or {}).get("exchange") or "").upper().strip()

        if ex=="AM":

            ex="NA"

        if ex not in ("NY","ND","NA"):

            _map={"SOXL":"NY","SOXS":"NY","SPY":"NY","TSM":"NY","ORCL":"NY",

                  "NVDA":"ND","AMD":"ND","AVGO":"ND","QQQ":"ND","TQQQ":"ND",

                  "SQQQ":"ND","PLTR":"ND","ARM":"ND","INTC":"ND","SMH":"ND"}

            ex=_map.get(sym,"NY")



        qty=max(1,int(os.getenv("WILLIAMS_KIWOOM_US_MOCK_QTY","1") or 1))

        cross=max(0.001,float(os.getenv("WILLIAMS_KIWOOM_US_MOCK_CROSS_PCT","0.01") or 0.01))

        b=KiwoomUSMockBroker()



        if action=="BUY":

            limit=round(price*(1.0+cross),2 if price>=1 else 4)

            r=b.buy_limit(sym,qty,limit,ex)

        elif action=="SELL":

            bal=b.balance(sym,ex)

            rows=bal.get("result_list") or []

            held=0

            for x in rows:

                if str(x.get("stk_cd") or "").upper()==sym:

                    try:

                        held=int(str(x.get("sell_alowq") or x.get("poss_qty") or "0"))

                    except Exception:

                        held=0

            if held <= 0:

                logging.warning("USA_MOCK_SELL_SKIP no holding sym=%s",sym)

                return None

            qty=min(qty,held)

            limit=round(price*(1.0-cross),2 if price>=1 else 4)

            r=b.sell_limit(sym,qty,limit,ex)

        else:

            return None



        logging.warning("USA_MOCK_%s_ACCEPTED sym=%s ex=%s qty=%s price=%s resp=%s",

                        action,sym,ex,qty,price,r)

        return r



    def _paper_williams_step(self, market, row):
        """Paper-only Williams execution bridge. Never calls a real broker."""
        # V145_USA_FROZEN_PAPER_AUTHORITY: isolated frozen USA paper path.
        if str(market).upper()=='USA':
            ev=self._v140_usa_frozen_williams_eval(row)
            row['williams_frozen_eval']=ev
            sym=str((row or {}).get('symbol') or '').upper()
            price=_f((row or {}).get('price'))
            # V161_FROZEN_WEAK_RUN_STATE: carry causal 2-bar combo state across refreshes.
            if sym and isinstance(ev,dict) and isinstance(ev.get('exit_eval'),dict):
                self._last[('WUF_WEAK',sym)]={'run':int(ev['exit_eval'].get('weak_run') or 0)}
            if not sym or price<=0:
                return None
            # Existing paper ledger only; never broker/Kiwoom.
            pos=self.paper.position('USA',sym) if hasattr(self.paper,'position') else None
            if pos:
                if bool(ev.get('exit')):
                    try:
                        self._usa_mock_order_step("SELL", row)
                    except Exception as e:
                        import logging as _logging
                        _logging.exception("USA_MOCK_SELL_ERROR sym=%s err=%s", sym, e)
                    return self.paper.exit('USA',sym,price,reason='WILLIAMS_FROZEN_EXIT')
                return self.paper.mark('USA',sym,price,state='HOLD')
            if bool(ev.get('entry')):
                # hard safety: no duplicate open symbol and cap open paper positions.
                try:
                    opens=self.paper.positions('USA') if hasattr(self.paper,'positions') else []
                except Exception:
                    opens=[]
                if any(str((p or {}).get('symbol','')).upper()==sym for p in (opens or [])):
                    return None
                max_pos=max(1,int(os.getenv('WILLIAMS_USA_PAPER_MAX_POSITIONS','5') or 5))
                if len(opens or [])>=max_pos:
                    return None
                try:
                    self._usa_mock_order_step("BUY", row)
                except Exception as e:
                    import logging as _logging
                    _logging.exception("USA_MOCK_BUY_ERROR sym=%s err=%s", sym, e)
                return self.paper.enter('USA',sym,price,strategy_id='WILLIAMS_FROZEN_V136',reason='WILLIAMS_FROZEN_ENTRY')
            return None
        market=market.upper(); sym=str(row.get('symbol') or '')
        if not sym:return None
        price=_f(row.get('price'))
        if price<=0:return None
        try:
            pos=next((p for p in self.paper.account(market).get('positions',[]) if str(p.get('symbol'))==sym),None)
            # Existing paper position: mark structure and close only on frozen STRUCT0 break.
            if pos:
                support=row.get('williams_support')
                st=row.get('williams_struct_state') or 'HOLD'
                self.paper.mark(market,sym,price,support=support,support_updates=row.get('williams_support_updates'),state=st)
                if bool(row.get('williams_exit_ready')):
                    return self.paper.exit(market,sym,price,reason='SUPPORT_BREAK_EXIT',support=support)
                return {'ok':True,'action':'HOLD','market':market,'symbol':sym,'price':price,'support':support}
            # New paper entry only when Williams exact evaluator has produced a fresh ENTRY signal on the row.
            wentry=bool(row.get('williams_entry') or row.get('williams_signal_entry'))
            if wentry and row.get('session')=='REGULAR':
                # V233: pre-entry STRUCT0 support is diagnostic only.  A long position
                # must never inherit a support at/above the entry price.  Post-entry
                # structure will establish/ratchet support from subsequent bars.
                _pre_support=_f(row.get('williams_support'))
                _entry_support=_pre_support if (0 < _pre_support < price) else None
                return self.paper.enter(market,sym,price,strategy_id='WILLIAMS_STRUCT0',reason='WILLIAMS_ENTRY',support=_entry_support)
        except Exception as e:
            return {'ok':False,'action':'ERROR','reason':f'{type(e).__name__}: {e}'}
        return None

    def _williams_mock_sync_account(self, broker):
        """V115: restore current Kiwoom mock holdings once per process."""
        if getattr(self, "_williams_mock_account_synced", False):
            return

        from datetime import datetime as _dt

        bal = broker.request_account(
            "kt00004",
            {"qry_tp":"0", "dmst_stex_tp":"KRX"},
        )
        fills = broker.request_account(
            "ka10076",
            {"qry_tp":"0", "sell_tp":"0", "stex_tp":"1"},
        )

        latest_buy = {}
        for x in fills.get("cntr", []) or []:
            if "+매수" not in str(x.get("io_tp_nm") or ""):
                continue
            sym = str(x.get("stk_cd") or "").replace("A", "").zfill(6)
            tm = str(x.get("ord_tm") or "").strip()
            if sym and tm and sym not in latest_buy:
                latest_buy[sym] = tm

        now = _dt.now(_WILLIAMS_KST)
        restored = 0
        for x in bal.get("stk_acnt_evlt_prst", []) or []:
            sym = str(x.get("stk_cd") or "").replace("A", "").zfill(6)
            try:
                qty = int(str(x.get("rmnd_qty") or "0").replace(",", ""))
            except Exception:
                qty = 0
            if not sym or qty <= 0:
                continue
            try:
                avg = float(str(x.get("avg_prc") or "0").replace(",", ""))
            except Exception:
                avg = 0.0

            entered_ts = 0.0
            tm = latest_buy.get(sym)
            if tm and len(tm) >= 6:
                try:
                    entered = now.replace(
                        hour=int(tm[0:2]),
                        minute=int(tm[2:4]),
                        second=int(tm[4:6]),
                        microsecond=0,
                    )
                    entered_ts = entered.timestamp()
                except Exception:
                    entered_ts = 0.0

            entered_bar_time=(now.strftime('%Y%m%d') + tm) if tm and len(tm)>=6 else now.strftime('%Y%m%d%H%M%S')
            self._last[("WILLIAMS_MOCK", sym)] = {
                "in_pos": True,
                "qty": qty,
                "entry_price": avg,
                "entered_ts": entered_ts,
                "entered_bar_time": entered_bar_time,
                "synced_from_account": True,
            }
            restored += 1

        self._williams_mock_account_synced = True
        import logging as _logging
        _logging.warning("WILLIAMS_MOCK_ACCOUNT_SYNC open_positions=%s", restored)

    def _williams_mock_held_symbols(self):
        """V119: currently open Kiwoom mock lifecycle symbols."""
        out=[]
        for k,st in list(self._last.items()):
            if not (isinstance(k,tuple) and len(k)==2 and k[0]=="WILLIAMS_MOCK"):
                continue
            if not isinstance(st,dict) or not st.get("in_pos"):
                continue
            sym=str(k[1] or '').replace('A','').zfill(6)
            if sym and sym not in out:
                out.append(sym)
        return out

    def _williams_mock_auto_step(self, row):
        import os
        auto_flag=(os.getenv("WILLIAMS_KIWOOM_MOCK_AUTO") or os.getenv("KIWOOM_MOCK_AUTO_ENABLED") or "0").lower()
        if auto_flag not in ("1","true","yes","on"):
            return
        if (row.get("session") or "") != "REGULAR":
            return
        sym=str(row.get("symbol") or "").zfill(6)
        if not sym:
            return
        try:
            from live_server.kiwoom_mock_broker import KiwoomMockBroker
            b=KiwoomMockBroker()
            if not b.cfg.order_enable:
                return
            self._williams_mock_sync_account(b)
            key=("WILLIAMS_MOCK",sym)
            st=self._last.get(key,{})
            in_pos=bool(st.get("in_pos"))
            entry=bool(row.get("williams_entry") or row.get("williams_signal_entry"))
            exit_ready=bool(row.get("williams_exit_ready"))
            # V233_STRUCT5_PRICE_GUARD: order-time price must still be above the detected STRUCT5 resistance.
            if entry and not in_pos and bool(row.get("williams_struct5_signal")):
                _s5_res=_f(row.get("williams_struct5_resistance"))
                _live_px=_f(row.get("price"))
                if _s5_res>0 and _live_px<=_s5_res:
                    return
            if entry and not in_pos:
                # V118: pre-entry whole-day EXIT telemetry does not veto a fresh ENTRY.
                # Once BUY is accepted, subsequent rows use only post-entry structure.
                # Retry guard: avoid hammering Kiwoom if a pending breakout survives multiple refreshes.
                import time as _time
                retry_key=("WILLIAMS_MOCK_RETRY",sym)
                last_try=self._last.get(retry_key) or {}
                if (_time.time()-_f(last_try.get("ts"),0)) < 15.0:
                    return
                self._last[retry_key]={"ts":_time.time()}
                import time as _time
                capital=float(os.getenv("WILLIAMS_MOCK_CAPITAL_KRW","1000000") or 1000000)
                max_positions=max(1,int(os.getenv("WILLIAMS_MOCK_MAX_POSITIONS","5") or 5))
                price=_f(row.get("price"))
                if price<=0:
                    return

                # V233: STRUCT5 is a fresh 5-bar resistance breakout.  Never submit a
                # mock BUY if the live order price has already fallen back to/below the
                # resistance that generated the signal.  This prevents stale/misaligned
                # chart-vs-quote snapshots such as signal resistance 6400 with order 6360.
                if bool(row.get('williams_struct5_signal')):
                    _s5_res=_f(row.get('williams_struct5_resistance'))
                    if _s5_res > 0 and price <= _s5_res:
                        import logging as _logging
                        _logging.warning("WILLIAMS_MOCK_ENTRY_BLOCKED_STRUCT5_PRICE sym=%s price=%s resistance=%s",sym,price,_s5_res)
                        self.store.event("KOREA",sym,"WILLIAMS_MOCK_ENTRY_BLOCKED",None,"BLOCKED",power=_f(row.get("power")),message=f'{sym} STRUCT5 live price no longer above resistance',payload={"row":row,"price":price,"resistance":_s5_res})
                        return

                # Reserve capital for positions opened by this bridge in the current process.
                reserved=0.0
                open_count=0
                for _k,_st in list(self._last.items()):
                    if not (isinstance(_k,tuple) and len(_k)>=2 and _k[0]=="WILLIAMS_MOCK"):
                        continue
                    if not isinstance(_st,dict) or not _st.get("in_pos"):
                        continue
                    open_count+=1
                    reserved += _f(_st.get("entry_price"))*_f(_st.get("qty"),1)
                if open_count>=max_positions:
                    return
                available=max(0.0,capital-reserved)
                if available < price:
                    return
                slot_budget=min(capital/max_positions,available)
                qty=int(slot_budget//price)
                if qty<1:
                    qty=1
                if qty*price>available:
                    return

                # V233_STRUCT5_LIVE_PRICE_GUARD: fail closed if live price no longer confirms breakout.
                if bool(row.get("williams_struct5_signal")):
                    resistance=_f(row.get("williams_struct5_resistance"))
                    if resistance>0 and not (price>resistance):
                        import logging as _logging
                        _logging.warning("WILLIAMS_MOCK_BUY_BLOCKED_STRUCT5_PRICE_SYNC sym=%s price=%s resistance=%s",sym,price,resistance)
                        self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY_BLOCKED",None,"BLOCKED",power=_f(row.get("power")),message=f'{sym} STRUCT5 price-sync blocked',payload={"row":row,"price":price,"resistance":resistance})
                        return

                r=b.buy_market(sym,qty)
                order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={
                    "in_pos":True,
                    "buy_order_no":order_no,
                    "qty":qty,
                    "entry_price":price,
                    "entered_ts":_time.time(),
                    "entered_bar_time":_dt.now(_WILLIAMS_KST).strftime('%Y%m%d%H%M%S'),
                }
                if row.get("williams_struct5_signal"):
                    day_key=_dt.now(_WILLIAMS_KST).strftime('%Y%m%d')
                    s5=_WILLIAMS_STATE[(str(sym),day_key)]
                    s5['struct5_order_sent']=True
                    s5['struct5_order_no']=order_no
                    s5['struct5_order_acked_at']=_dt.now(_WILLIAMS_KST)
                import logging as _logging
                _logging.warning("WILLIAMS_MOCK_BUY_ACCEPTED sym=%s qty=%s price=%s order_no=%s struct5=%s",sym,qty,price,order_no,bool(row.get("williams_struct5_signal")))
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY {qty}',payload={"order":r,"row":row,"qty":qty,"entry_price":price})
            elif in_pos:
                import time as _time
                qty=max(1,int(_f(st.get("qty"),1)))
                entry_price=_f(st.get("entry_price"))
                price=_f(row.get("price"))
                entered_ts=_f(st.get("entered_ts"))
                hold_sec=(_time.time()-entered_ts) if entered_ts else 999999.0
                hard_stop=bool(entry_price and price and price<=entry_price*0.985)

                # V118: row EXIT_READY is now computed from bars strictly after
                # this position's BUY minute. Pre-entry support cannot trigger this exit.
                # Emergency -1.5% hard stop remains independent and immediate.
                if not hard_stop:
                    if not exit_ready:
                        return
                    if hold_sec < 300.0:
                        return

                r=b.sell_market(sym,qty)
                sell_order_no=r.get("ord_no") or r.get("order_no")
                self._last[key]={"in_pos":False,"sell_order_no":sell_order_no,"qty":qty,"entry_price":entry_price,"entered_ts":entered_ts}
                import logging as _logging
                _logging.warning("WILLIAMS_MOCK_SELL_ACCEPTED sym=%s qty=%s price=%s hold_sec=%.1f hard_stop=%s order_no=%s",sym,qty,price,hold_sec,hard_stop,sell_order_no)
                self.store.event("KOREA",sym,"WILLIAMS_MOCK_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock SELL {qty}',payload={"order":r,"row":row,"qty":qty,"entry_price":entry_price,"hold_sec":hold_sec,"hard_stop":hard_stop})
        except Exception as e:
            import logging as _logging
            _logging.exception("WILLIAMS_MOCK_ERROR sym=%s error=%s",sym,e)
            self.store.event("KOREA",sym,"WILLIAMS_MOCK_ERROR",None,"ERROR",power=_f(row.get("power")),message=str(e),payload={"row":row})

    def _finalize(self,market,rows):
        rows=rows[:TRACK_LIMIT]
        for trank,r in enumerate(rows,1):
            r['tracker_rank']=trank
            sym=r['symbol']; state=r['state']; power=_f(r['power']); prev=self._last.get((market,sym),{}); ps=prev.get('state'); pp=_f(prev.get('power')); pr=self._rank.get((market,sym))
            # V4.9.0C-4C Q2 SHADOW ENTRY LOGGER SAFE3
            q2_shadow=r.get('rebound_shadow') or {}
            q2_state=q2_shadow.get('rebound_state_q2_shadow')
            prev_q2_state=prev.get('q2_state')
            if ps and ps!=state:self.store.event(market,sym,'STATE_CHANGE',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} {ps}→{state}',payload=r)
            elif prev and abs(power-pp)>=POWER_ALERT_DELTA:self.store.event(market,sym,'POWER_JUMP',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} Power {pp:.0f}→{power:.0f}',payload=r)
            elif pr is not None and abs(pr-trank)>=RANK_ALERT_DELTA:self.store.event(market,sym,'TRACKER_RANK_MOVE',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} 실시간 순위 {pr}→{trank}',payload=r)
            if market=='USA' and prev_q2_state and prev_q2_state!=q2_state and q2_state=='REBOUND_ENTRY':
                self.store.event(
                    market,
                    sym,
                    'Q2_REBOUND_ENTRY_SHADOW',
                    prev_q2_state,
                    q2_state,
                    power=power,
                    rank_from=pr,
                    rank_to=trank,
                    message=f'{sym} Q2 {prev_q2_state}→{q2_state}',
                    payload=r
                )
            self._last[(market,sym)]={'state':state,'power':power,'q2_state':q2_state}; self._last[('POWER',market,sym)]={'power':power}; self._rank[(market,sym)]=trank; minute=r['updated_at'][:16]
            if self._snap.get((market,sym))!=minute:
                self.store.snapshot(r)
                if market=='USA' and r.get('session')=='REGULAR' and (r.get('data_integrity') or {}).get('valid'):
                    self.store.add_validation_mark(r)
                elif market=='KOREA' and r.get('session')=='REGULAR':
                    self.store.add_korea_validation_mark(r)
                self._snap[(market,sym)]=minute
            if market=='USA' and r.get('session')=='REGULAR' and (r.get('data_integrity') or {}).get('valid'):
                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))
            elif market=='KOREA' and r.get('session')=='REGULAR':
                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))
                self._williams_mock_auto_step(r)
            # V171_SINGLE_USA_PAPER_AUTHORITY: dedicated frozen19 loop owns USA paper evaluation.
            paper_result=None if (market=='USA' and getattr(self,'_frozen_universe_loop_enabled',False)) else self._paper_williams_step(market,r)
            if paper_result is not None:r['paper_williams']=paper_result
        sess=_session(market)
        self.tracker[market]={'rows':rows,'updated_at':_now(),'session':sess,'tracked_count':len(rows),'max_tracked':TRACK_LIMIT,'is_live':sess=='REGULAR','power_basis':'LIVE_REGULAR' if sess=='REGULAR' else 'LAST_AVAILABLE_REFERENCE','policy':'OPEN POSITIONS first; remaining slots use live readiness/power, then Finder rank. Maximum 5 heavy-tracked symbols.'}
        # V4.9.0C-4D.1 Q2 SCAN STATS OUTPUT SAFE2
        if market=='USA':
            self.tracker[market]['q2_universe_scan']=getattr(self,'_q2_universe_scan_stats',None)
    def status(self,market):
        market=market.upper(); return {'market':market,'session':_session(market),'finder':self.finder.get(market),'tracker':self.tracker.get(market),'positions':self.store.positions(market),'paper_account':self.paper.account(market),'paper_trades':self.paper.trades(market,20),'events':self.store.events(market,20),'version':'V4_CLEAN_ENGINE_ALPHA'}


# === WILLIAMS LIVE EVALUATOR V23 ===
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
from collections import defaultdict as _dd

_WILLIAMS_KST = _tz(_td(hours=9))
_WILLIAMS_STATE = _dd(dict)

def _williams_rsi2(closes):
    if len(closes) < 3:
        return None
    gains = []
    losses = []
    for i in range(1, 3):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains) / 2.0
    al = sum(losses) / 2.0
    r = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    for i in range(3, len(closes)):
        d = closes[i] - closes[i-1]
        g = max(d, 0.0)
        l = max(-d, 0.0)
        ag = (ag + g) / 2.0
        al = (al + l) / 2.0
        r = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))
    return r

def williams_live_evaluate_v23(
    symbol,
    prev_day_high,
    prev_day_low,
    day_open,
    prev_price,
    current_price,
    recent_closes,
    finder_rank=None,
    now=None,
):
    """
    Returns a pure evaluation dict.
    Does not place orders and does not mutate broker state.
    """
    now = now or _dt.now(_WILLIAMS_KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_WILLIAMS_KST)
    else:
        now = now.astimezone(_WILLIAMS_KST)

    day_key = now.strftime("%Y%m%d")
    trigger = float(day_open) + 0.5 * (float(prev_day_high) - float(prev_day_low))
    rsi2 = _williams_rsi2([float(x) for x in recent_closes])

    st = _WILLIAMS_STATE[(str(symbol), day_key)]
    armed_at = st.get("armed_at")
    sent = bool(st.get("signal_sent"))

    raw_cross = (
        float(prev_price) <= trigger < float(current_price)
        and rsi2 is not None
        and rsi2 > 50.0
    )

    if raw_cross and armed_at is None and not sent:
        armed_at = now
        st["armed_at"] = now

    age_min = None
    if armed_at is not None:
        age_min = (now - armed_at).total_seconds() / 60.0
        if age_min > 30.0 and not sent:
            st.pop("armed_at", None)
            armed_at = None
            age_min = None

    finder_ok = finder_rank is not None and int(finder_rank) <= 20
    signal = bool(
        armed_at is not None
        and age_min is not None
        and 0.0 <= age_min <= 30.0
        and finder_ok
        and not sent
    )

    if signal:
        st["signal_sent"] = True
        st["confirmed_at"] = now

    if sent:
        stage = "SIGNAL_SENT"
    elif signal:
        stage = "ENTRY_CANDIDATE"
    elif armed_at is not None:
        stage = "READY"
    else:
        stage = "WATCH"

    return {
        "engine_id": "williams",
        "engine_name": "윌리암스",
        "status": "VALIDATION_CANDIDATE",
        "selectable": False,
        "orders_enabled": False,
        "symbol": str(symbol),
        "trigger": trigger,
        "rsi2": rsi2,
        "raw_cross": raw_cross,
        "finder_rank": finder_rank,
        "finder_confirmed": finder_ok,
        "armed_at": armed_at.isoformat() if armed_at else None,
        "age_min": age_min,
        "stage": stage,
        "signal": signal,
        "rule": "CrossUp(day_open+0.5*(prev_high-prev_low)) & RSI2>50 -> Finder rank<=20 within 30m -> first next 1m bar entry candidate -> 5m validation hold",
        "max_one_signal_per_symbol_day": True,
    }

