from __future__ import annotations
import json, math, sqlite3, threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from .analytics import ticks_to_bars, multi_timeframe_signal

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
    """V1 long-entry gate.

    5m = setup/trend.
    1m = actual trigger.
    The score is diagnostic only, not a probability.
    """
    setup_checks={}
    trigger_checks={}
    if len(b5)>=3:
        c0=_f(b5.iloc[-1]['close']); c1=_f(b5.iloc[-2]['close']); c2=_f(b5.iloc[-3]['close'])
        l0=_f(b5.iloc[-1]['low']); l1=_f(b5.iloc[-2]['low'])
        setup_checks={
            'price_above_vwap': bool(price and vwap and price>vwap),
            'ema9_above_ema20': bool(ema9 and ema20 and ema9>ema20),
            'five_min_rising': bool(c0>c1),
            'five_min_structure': bool((c0>c1>c2) or (l0>l1 and c0>=c1)),
        }
    else:
        setup_checks={'price_above_vwap':False,'ema9_above_ema20':False,'five_min_rising':False,'five_min_structure':False}

    if len(b1)>=3:
        last=b1.iloc[-1]; prev=b1.iloc[-2]
        lc=_f(last['close']); lo=_f(last['open']); ph=_f(prev['high']); pc=_f(prev['close'])
        one_ret=((lc/pc-1)*100) if pc else 0
        trigger_checks={
            'green_1m': bool(lc>lo),
            'break_prev_high': bool(lc>ph),
            'volume_expansion': bool(vol_ratio>=1.5),
            'one_min_impulse': bool(one_ret>=0.15),
            'power_acceleration': bool(power>=60 and delta>=4),
        }
    else:
        one_ret=0.0
        trigger_checks={'green_1m':False,'break_prev_high':False,'volume_expansion':False,'one_min_impulse':False,'power_acceleration':False}

    setup_count=sum(1 for v in setup_checks.values() if v)
    trigger_count=sum(1 for v in trigger_checks.values() if v)
    setup_ok=setup_count>=3
    # ENTRY requires a real 1m breakout + participation + accelerating Power.
    trigger_core=bool(trigger_checks.get('green_1m') and trigger_checks.get('break_prev_high')
                      and trigger_checks.get('volume_expansion') and trigger_checks.get('power_acceleration'))
    chase_ok=bool(risk=='NORMAL' and rsi<74 and over_vwap<2.5)
    ready=bool(setup_ok and trigger_count>=3 and power>=55 and chase_ok)
    entry=bool(setup_ok and trigger_core and power>=68 and delta>=4 and chase_ok)

    return {
        'setup_ok':setup_ok,
        'ready':ready,
        'entry':entry,
        'setup_count':setup_count,
        'setup_total':len(setup_checks),
        'trigger_count':trigger_count,
        'trigger_total':len(trigger_checks),
        'setup_checks':setup_checks,
        'trigger_checks':trigger_checks,
        'one_min_return_pct':round(one_ret,3),
        'chase_ok':chase_ok,
        'rule':'5m Setup + 1m breakout/volume + Power acceleration + chase guard',
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
        self.store=V4Store(db_path); self.finder={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self.tracker={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self._last={}; self._snap={}; self._rank={}; self._lock=threading.RLock()
    def build_usa_finder(self,candidates,discovery,limit=5,db=None,commit=True,shadow_allow_unknown_quality=False):
        qmap={str(r.get('symbol') or '').upper():r for r in (discovery.get('rows') or [])}
        rows=[]
        inverse_syms={'SOXS','SQQQ'}
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
            if price<5 or dv<20_000_000 or atr<=0 or atr>12:continue

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
        light_rows=rows[:20]
        for i,r in enumerate(light_rows,1):
            r['light_rank']=i

        # Heavy Tracker receives the best 5 actionable names from Light20.
        # C_HIGH_RISK names remain visible in Light20; they can enter Heavy5 only
        # while the strict continuation test is true.
        heavy_pool=[
            r for r in light_rows
            if r.get('quality')!='C_HIGH_RISK' or r.get('extreme_continue')
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
    def refresh_usa_tracker(self,db):
        syms=self.tracked_symbols('USA'); fmap={r['symbol']:r for r in self.finder['USA']['rows']}; pmap={p['symbol']:p for p in self.store.positions('USA')}; qmap={q.get('symbol'):q for q in db.quotes()}; rows=[]
        for sym in syms:rows.append(self._usa_row(sym,db.ticks(sym,40000),multi_timeframe_signal(sym,db.ticks(sym,40000),db.quotes()),qmap,fmap.get(sym),pmap.get(sym)))
        rows.sort(key=_tracker_sort_key); self._finalize('USA',rows); return self.tracker['USA']
    def _usa_row(self,sym,ticks,sig,qmap,finder,pos):
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
        return {'market':'USA','symbol':sym,'name':(finder or {}).get('name') or sym,'finder_rank':(finder or {}).get('rank'),'finder_score':(finder or {}).get('finder_score'),'position_open':bool(pos),'qty':_f((pos or {}).get('qty')),'avg_entry':_f((pos or {}).get('avg_entry')),'price':price,'direction':'LONG' if power>=18 else 'SHORT' if power<=-18 else 'NEUTRAL','power':power,'power_delta':delta,'power_label':('강한 상승' if power>=70 else '상승 우세' if power>=30 else '중립' if power>-30 else '하락 우세' if power>-70 else '강한 하락'),'state':state,'risk':risk,'data_integrity':integrity,'entry_gate':entry_gate,'position_gate':position_gate,'raw_power_before_gate':raw_power,'components':{'structure':round(structure,1),'volume':round(volume,1),'momentum':round(momentum,1),'market_sector':round(market,1),'risk_penalty':round(penalty,1),'rvol':round(rvol,2),'volume_ratio':round(vol_ratio,2),'vwap':vwap or None,'ema9':ema9 or None,'ema20':ema20 or None,'rsi':round(rsi,1)},'warning_floor':warn,'hard_floor':hard,'target1':t1,'target2':t2,'floor_mode':mode,'reason':final_reason,'session':sess,'updated_at':_now()}
    def refresh_korea_tracker(self,korea):
        syms=self.tracked_symbols('KOREA'); fmap={r['symbol']:r for r in self.finder['KOREA']['rows']}; pmap={p['symbol']:p for p in self.store.positions('KOREA')}; pulse={str(r.get('symbol') or ''):r for r in (korea.intraday_pulse.get('rows') or [])}; rows=[]
        for sym in syms:
            f=fmap.get(sym) or {}; p=pulse.get(sym) or {}; strength=p.get('strength_composite'); score=_f(p.get('live_score',f.get('finder_score'))); bias=str(p.get('bias') or f.get('direction') or 'NEUTRAL').upper(); sc=_clip((_f(strength)-100)/35,-1,1)*45 if strength is not None else 0; ss=_clip((score-50)/50,-1,1)*40; sign=1 if bias in ('LONG','UP') else -1 if bias in ('SHORT','DOWN') else 0; power=round(_clip(sign*abs(ss)+sc,-100,100),1); prev=self._last.get(('POWER','KOREA',sym)); delta=round(power-_f(prev.get('power')),1) if prev else 0; vi=bool(p.get('vi_triggered')); risk='HIGH' if vi else str(f.get('risk') or 'NORMAL'); state='HOLD' if pmap.get(sym) else ('SETUP' if abs(power)>=55 and _session('KOREA')=='REGULAR' else 'WATCH')
            rows.append({'market':'KOREA','symbol':sym,'name':f.get('name') or sym,'finder_rank':f.get('rank'),'finder_score':f.get('finder_score'),'position_open':bool(pmap.get(sym)),'qty':_f((pmap.get(sym) or {}).get('qty')),'avg_entry':_f((pmap.get(sym) or {}).get('avg_entry')),'price':_f(p.get('price',f.get('price'))),'direction':'LONG' if power>=18 else 'SHORT' if power<=-18 else 'NEUTRAL','power':power,'power_delta':delta,'power_label':('강한 상승' if power>=70 else '상승 우세' if power>=30 else '중립' if power>-30 else '하락 우세' if power>-70 else '강한 하락'),'state':state,'risk':risk,'components':{'execution_strength':strength,'live_score':score,'minute_chart_gate':False},'warning_floor':None,'hard_floor':None,'target1':None,'target2':None,'floor_mode':'PENDING','reason':'체결강도/후보점수 기반 · 국내 1/5분봉 Gate 연결 전','session':_session('KOREA'),'updated_at':_now()})
        rows.sort(key=_tracker_sort_key); self._finalize('KOREA',rows); return self.tracker['KOREA']
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
    def _finalize(self,market,rows):
        rows=rows[:TRACK_LIMIT]
        for trank,r in enumerate(rows,1):
            r['tracker_rank']=trank
            sym=r['symbol']; state=r['state']; power=_f(r['power']); prev=self._last.get((market,sym),{}); ps=prev.get('state'); pp=_f(prev.get('power')); pr=self._rank.get((market,sym))
            if ps and ps!=state:self.store.event(market,sym,'STATE_CHANGE',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} {ps}→{state}',payload=r)
            elif prev and abs(power-pp)>=POWER_ALERT_DELTA:self.store.event(market,sym,'POWER_JUMP',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} Power {pp:.0f}→{power:.0f}',payload=r)
            elif pr is not None and abs(pr-trank)>=RANK_ALERT_DELTA:self.store.event(market,sym,'TRACKER_RANK_MOVE',ps,state,power=power,rank_from=pr,rank_to=trank,message=f'{sym} 실시간 순위 {pr}→{trank}',payload=r)
            self._last[(market,sym)]={'state':state,'power':power}; self._last[('POWER',market,sym)]={'power':power}; self._rank[(market,sym)]=trank; minute=r['updated_at'][:16]
            if self._snap.get((market,sym))!=minute:
                self.store.snapshot(r)
                if market=='USA' and r.get('session')=='REGULAR' and (r.get('data_integrity') or {}).get('valid'):
                    self.store.add_validation_mark(r)
                self._snap[(market,sym)]=minute
            if market=='USA' and r.get('session')=='REGULAR' and (r.get('data_integrity') or {}).get('valid'):
                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))
        sess=_session(market)
        self.tracker[market]={'rows':rows,'updated_at':_now(),'session':sess,'tracked_count':len(rows),'max_tracked':TRACK_LIMIT,'is_live':sess=='REGULAR','power_basis':'LIVE_REGULAR' if sess=='REGULAR' else 'LAST_AVAILABLE_REFERENCE','policy':'OPEN POSITIONS first; remaining slots use live readiness/power, then Finder rank. Maximum 5 heavy-tracked symbols.'}
    def status(self,market):
        market=market.upper(); return {'market':market,'session':_session(market),'finder':self.finder.get(market),'tracker':self.tracker.get(market),'positions':self.store.positions(market),'events':self.store.events(market,20),'version':'V4_CLEAN_ENGINE_ALPHA'}
