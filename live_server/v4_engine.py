from __future__ import annotations
import json, math, sqlite3, threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
from .analytics import ticks_to_bars, multi_timeframe_signal

SEMI={'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'}
TRACK_LIMIT=5

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

class V4Store:
    def __init__(self,path): self.path=path; self._init()
    def _c(self):
        c=sqlite3.connect(self.path,timeout=20); c.row_factory=sqlite3.Row; return c
    def _init(self):
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS v4_positions(market TEXT NOT NULL,symbol TEXT NOT NULL,qty REAL NOT NULL DEFAULT 0,avg_entry REAL NOT NULL DEFAULT 0,realized_pnl REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'OPEN',opened_at TEXT,updated_at TEXT,closed_at TEXT,PRIMARY KEY(market,symbol));
            CREATE TABLE IF NOT EXISTS v4_trade_log(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,qty REAL NOT NULL,price REAL NOT NULL,realized_pnl REAL NOT NULL DEFAULT 0,note TEXT);
            CREATE TABLE IF NOT EXISTS v4_signal_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT,event_type TEXT NOT NULL,state_from TEXT,state_to TEXT,power REAL,rank_from INTEGER,rank_to INTEGER,message TEXT,payload_json TEXT);
            CREATE TABLE IF NOT EXISTS v4_tracker_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT NOT NULL,market TEXT NOT NULL,symbol TEXT NOT NULL,finder_rank INTEGER,power REAL,power_delta REAL,state TEXT,risk TEXT,price REAL,payload_json TEXT);
            """)
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

class CleanEngine:
    def __init__(self,db_path):
        self.store=V4Store(db_path); self.finder={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self.tracker={m:{'rows':[],'updated_at':None} for m in ('USA','KOREA')}; self._last={}; self._snap={}; self._lock=threading.RLock()
    def build_usa_finder(self,candidates,discovery,limit=5):
        qmap={str(r.get('symbol') or '').upper():r for r in (discovery.get('rows') or [])}; rows=[]
        for c in candidates or []:
            sym=str(c.get('symbol') or '').upper(); q=qmap.get(sym) or {}; quality=q.get('quality_grade')
            if quality not in ('A','B_EVENT'):continue
            price=_f(c.get('price')); dv=_f(c.get('dollar_volume')); rvol=_f(c.get('rvol')); atr=abs(_f(c.get('atr_pct'))); chg=_f(c.get('change_pct'))
            if price<5 or dv<20_000_000 or atr<=0 or atr>12:continue
            liq=_clip((math.log10(max(dv,1))-7.3)/2*25,0,25); act=_clip((rvol-.5)/2.5*25,0,25); vol=20 if 2<=atr<=7 else 14 if 1<=atr<=10 else 7; directional=_clip(abs(chg)/8*15,0,15); qp=15 if quality=='A' else 9; chase=12 if abs(chg)>=15 else 6 if abs(chg)>=10 else 0
            rows.append({'market':'USA','symbol':sym,'name':q.get('name') or c.get('name') or sym,'quality':quality,'finder_score':round(_clip(liq+act+vol+directional+qp-chase,0,100),1),'direction':'UP' if chg>=0 else 'DOWN','price':price,'change_pct':chg,'dollar_volume':dv,'rvol':rvol,'atr_pct':atr,'risk':'CHASE' if chase else 'NORMAL'})
        rows.sort(key=lambda r:(r['finder_score'],r['dollar_volume']),reverse=True); rows=rows[:limit]
        for i,r in enumerate(rows,1):r['rank']=i
        self._update_finder('USA',rows); return self.finder['USA']
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
        self._finalize('USA',rows); return self.tracker['USA']
    def _usa_row(self,sym,ticks,sig,qmap,finder,pos):
        b1=ticks_to_bars(ticks,1); b5=ticks_to_bars(ticks,5); price=_f((qmap.get(sym) or {}).get('price') or (finder or {}).get('price')); ind=sig.get('indicators') or {}; vwap=_f(ind.get('vwap')); ema9=_f(ind.get('ema9')); ema20=_f(ind.get('ema20')); rvol=_f(ind.get('rvol')); rsi=_f(ind.get('rsi14'),50)
        structure=(10 if price and vwap and price>vwap else -10 if price and vwap else 0)+(10 if ema9 and ema20 and ema9>ema20 else -10 if ema9 and ema20 else 0)
        if len(b5)>=2:structure+=10 if _f(b5.iloc[-1]['close'])>_f(b5.iloc[-2]['close']) else -10
        vol_ratio=1; micro=0
        if len(b1)>=4:
            vols=pd.to_numeric(b1['volume'],errors='coerce').fillna(0); base=max(_f(vols.iloc[-11:-1].mean() if len(vols)>=11 else vols.iloc[:-1].mean()),1); vol_ratio=_clip(_f(vols.iloc[-1])/base,0,8); micro=1 if _f(b1.iloc[-1]['close'])>=_f(b1.iloc[-1]['open']) else -1
        volume=micro*_clip(max(rvol,vol_ratio)-1,0,3)/3*25
        momentum=0; rets=[]
        if len(b1)>=6:
            closes=pd.to_numeric(b1['close'],errors='coerce'); now=_f(closes.iloc[-1])
            for n,w in ((1,.45),(3,.35),(5,.20)):
                prev=_f(closes.iloc[-1-n]); ret=(now/prev-1)*100 if prev else 0; rets.append(ret); momentum+=_clip(ret,-1,1)*(20*w)
        qqq=_f((qmap.get('QQQ') or {}).get('change_pct')); spy=_f((qmap.get('SPY') or {}).get('change_pct')); smh=_f((qmap.get('SMH') or {}).get('change_pct')); ref=(.6*qqq+.4*smh) if sym in SEMI else (.6*qqq+.4*spy); market=_clip(ref/1.5,-1,1)*15
        over=((price/vwap-1)*100) if price and vwap else 0; penalty=0; risk='NORMAL'
        if abs(over)>=3:penalty+=5; risk='CHASE'
        if rsi>=78 or rsi<=22:penalty+=3; risk='CHASE'
        if abs(sum(rets))>=4:penalty+=2; risk='HIGH'
        raw=structure+volume+momentum+market; power=_clip(raw,-100,100); power=power-penalty if power>0 else power+penalty if power<0 else 0; power=round(power,1)
        prev=self._last.get(('POWER','USA',sym)); delta=round(power-_f(prev.get('power')),1) if prev else 0
        five=bool(len(b5)>=2 and ((power>0 and _f(b5.iloc[-1]['close'])>=_f(b5.iloc[-2]['close'])) or (power<0 and _f(b5.iloc[-1]['close'])<=_f(b5.iloc[-2]['close'])))); regular=_session('USA')=='REGULAR'
        if pos:state=self._position_state(power,delta,price,pos)
        elif not regular:state='WATCH'
        elif abs(power)>=78 and five and risk=='NORMAL' and abs(delta)>=4:state='ENTRY'
        elif abs(power)>=65 and five and risk!='HIGH':state='READY'
        elif abs(power)>=48:state='SETUP'
        else:state='WATCH'
        hard,warn,t1,t2,mode=self._levels(price,pos,power,delta,vwap,ema20,b1,b5)
        reason=[]
        if structure>=15:reason.append('가격구조 상승')
        elif structure<=-15:reason.append('가격구조 하락')
        if abs(volume)>=8:reason.append('거래량 유입' if volume>0 else '매도 거래량')
        if abs(momentum)>=7:reason.append('모멘텀 상승' if momentum>0 else '모멘텀 하락')
        return {'market':'USA','symbol':sym,'name':(finder or {}).get('name') or sym,'finder_rank':(finder or {}).get('rank'),'finder_score':(finder or {}).get('finder_score'),'position_open':bool(pos),'qty':_f((pos or {}).get('qty')),'avg_entry':_f((pos or {}).get('avg_entry')),'price':price,'direction':'LONG' if power>=18 else 'SHORT' if power<=-18 else 'NEUTRAL','power':power,'power_delta':delta,'state':state,'risk':risk,'components':{'structure':round(structure,1),'volume':round(volume,1),'momentum':round(momentum,1),'market_sector':round(market,1),'risk_penalty':round(penalty,1),'rvol':round(rvol,2),'volume_ratio':round(vol_ratio,2),'vwap':vwap or None,'ema9':ema9 or None,'ema20':ema20 or None,'rsi':round(rsi,1)},'warning_floor':warn,'hard_floor':hard,'target1':t1,'target2':t2,'floor_mode':mode,'reason':' · '.join(reason[:3]) or '관찰 중','session':_session('USA'),'updated_at':_now()}
    def refresh_korea_tracker(self,korea):
        syms=self.tracked_symbols('KOREA'); fmap={r['symbol']:r for r in self.finder['KOREA']['rows']}; pmap={p['symbol']:p for p in self.store.positions('KOREA')}; pulse={str(r.get('symbol') or ''):r for r in (korea.intraday_pulse.get('rows') or [])}; rows=[]
        for sym in syms:
            f=fmap.get(sym) or {}; p=pulse.get(sym) or {}; strength=p.get('strength_composite'); score=_f(p.get('live_score',f.get('finder_score'))); bias=str(p.get('bias') or f.get('direction') or 'NEUTRAL').upper(); sc=_clip((_f(strength)-100)/35,-1,1)*45 if strength is not None else 0; ss=_clip((score-50)/50,-1,1)*40; sign=1 if bias in ('LONG','UP') else -1 if bias in ('SHORT','DOWN') else 0; power=round(_clip(sign*abs(ss)+sc,-100,100),1); prev=self._last.get(('POWER','KOREA',sym)); delta=round(power-_f(prev.get('power')),1) if prev else 0; vi=bool(p.get('vi_triggered')); risk='HIGH' if vi else str(f.get('risk') or 'NORMAL'); state='HOLD' if pmap.get(sym) else ('SETUP' if abs(power)>=55 and _session('KOREA')=='REGULAR' else 'WATCH')
            rows.append({'market':'KOREA','symbol':sym,'name':f.get('name') or sym,'finder_rank':f.get('rank'),'finder_score':f.get('finder_score'),'position_open':bool(pmap.get(sym)),'qty':_f((pmap.get(sym) or {}).get('qty')),'avg_entry':_f((pmap.get(sym) or {}).get('avg_entry')),'price':_f(p.get('price',f.get('price'))),'direction':'LONG' if power>=18 else 'SHORT' if power<=-18 else 'NEUTRAL','power':power,'power_delta':delta,'state':state,'risk':risk,'components':{'execution_strength':strength,'live_score':score,'minute_chart_gate':False},'warning_floor':None,'hard_floor':None,'target1':None,'target2':None,'floor_mode':'PENDING','reason':'체결강도/후보점수 기반 · 국내 1/5분봉 Gate 연결 전','session':_session('KOREA'),'updated_at':_now()})
        self._finalize('KOREA',rows); return self.tracker['KOREA']
    def _position_state(self,power,delta,price,pos):
        entry=_f(pos.get('avg_entry')); pnl=(price/entry-1)*100 if entry and price else 0
        if pnl<=-4:return 'STOP'
        if power<=5:return 'EXIT'
        if power<28 or delta<=-18:return 'REDUCE'
        if pnl>=2 and delta<=-10:return 'TAKE_PROFIT'
        return 'HOLD'
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
        for r in rows:
            sym=r['symbol']; state=r['state']; power=_f(r['power']); prev=self._last.get((market,sym),{}); ps=prev.get('state'); pp=_f(prev.get('power'))
            if ps and ps!=state:self.store.event(market,sym,'STATE_CHANGE',ps,state,power=power,message=f'{sym} {ps}→{state}',payload=r)
            elif prev and abs(power-pp)>=15:self.store.event(market,sym,'POWER_JUMP',ps,state,power=power,message=f'{sym} Power {pp:.0f}→{power:.0f}',payload=r)
            self._last[(market,sym)]={'state':state,'power':power}; self._last[('POWER',market,sym)]={'power':power}; minute=r['updated_at'][:16]
            if self._snap.get((market,sym))!=minute:self.store.snapshot(r); self._snap[(market,sym)]=minute
        self.tracker[market]={'rows':rows,'updated_at':_now(),'session':_session(market),'tracked_count':len(rows),'max_tracked':TRACK_LIMIT,'policy':'OPEN POSITIONS first, then Finder rank; maximum 5 heavy-tracked symbols.'}
    def status(self,market):
        market=market.upper(); return {'market':market,'session':_session(market),'finder':self.finder.get(market),'tracker':self.tracker.get(market),'positions':self.store.positions(market),'events':self.store.events(market,20),'version':'V4_CLEAN_ENGINE_ALPHA'}
