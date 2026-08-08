from __future__ import annotations
from datetime import datetime, timezone, timedelta
from statistics import mean
from pathlib import Path
import json, sqlite3
import pandas as pd
import requests

SEMI={'SOXL','SOXS','SMH','NVDA','AMD','AVGO','MU','ARM','TSM','ASML','INTC','QCOM'}

def num(v, default=0.0):
    try: return float(str(v).replace(',','').strip())
    except Exception: return default

def _atr5(prev_rows):
    trs=[]
    for i,r in enumerate(prev_rows):
        pc=prev_rows[i-1]['close'] if i>0 else r['close']
        trs.append(max(r['high']-r['low'],abs(r['high']-pc),abs(r['low']-pc)))
    m=mean([r['close'] for r in prev_rows]) if prev_rows else 0
    return mean(trs)/m*100 if m else 0

def open_model_score(open_px, prev5, gap_pct, qqq_gap=0.0, smh_gap=0.0, symbol=''):
    closes=[r['close'] for r in prev5]
    ma5=mean(closes); slope=(closes[-1]/closes[0]-1)*100 if closes[0] else 0
    avg5vol=mean([r['volume'] for r in prev5])
    avg5dv=mean([r['close']*r['volume'] for r in prev5])
    atr=_atr5(prev5); score=0.0; parts={}
    def add(name,pts):
        nonlocal score; score+=pts; parts[name]=round(pts,2)

    add('Open>MA5',14 if open_px>ma5 else -18)

    if slope>=2:add('MA5 slope',14)
    elif slope>0:add('MA5 slope',7+min(7,slope*3.5))
    elif slope<=-5:add('MA5 slope',-14)
    else:add('MA5 slope',slope*2.2)

    if avg5dv>=1_000_000_000:add('Liquidity',18)
    elif avg5dv>=500_000_000:add('Liquidity',15)
    elif avg5dv>=150_000_000:add('Liquidity',12)
    elif avg5dv>=50_000_000:add('Liquidity',7)
    elif avg5dv>=20_000_000:add('Liquidity',3)
    else:add('Liquidity',-10)

    if 3<=atr<=8:add('ATR',12)
    elif 1.5<=atr<3 or 8<atr<=10:add('ATR',8)
    elif 1<=atr<1.5:add('ATR',4)
    elif atr>12:add('ATR',-4)

    if .5<=gap_pct<=4:add('Gap momentum',12)
    elif 0<gap_pct<.5:add('Gap momentum',5)
    elif 4<gap_pct<=8:add('Gap momentum',7)
    elif abs(gap_pct)>12:add('Gap momentum',-12)
    elif gap_pct<0:add('Gap momentum',-6)

    mg=smh_gap if symbol in SEMI else qqq_gap
    if mg>=1:add('Market/Sector',8)
    elif mg>0:add('Market/Sector',4)
    elif mg<=-1:add('Market/Sector',-7)

    if symbol in {'SOXL','SOXS','TQQQ','SQQQ'}:add('Liquid leveraged ETF',5)
    elif symbol in {'NVDA','AAPL','MSFT','AMZN','META','TSLA','AMD','PLTR'}:add('Core liquidity',2)

    return {'score':max(0,min(100,round(score))),'ma5':ma5,'ma5_slope_pct':slope,
            'avg5_volume':avg5vol,'avg5_dollar_volume':avg5dv,'atr5_pct':atr,
            'gap_pct':gap_pct,'parts':parts}

class ValidationStore:
    def __init__(self, live_db_path:str):
        p=Path(live_db_path)
        self.path=str(p.with_name(p.stem+'_validation.db'))
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS runs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              model TEXT, days_requested INTEGER, days_validated INTEGER,
              symbols_requested INTEGER, mean_spearman REAL, median_spearman REAL,
              top5_excess_avg REAL, top5_close_avg REAL, top5_positive_excess_rate REAL,
              note TEXT, created_at TEXT, payload TEXT
            );
            """)

    def save(self,result:dict)->int:
        s=result['summary']
        with sqlite3.connect(self.path) as c:
            cur=c.execute("""INSERT INTO runs(model,days_requested,days_validated,symbols_requested,
                mean_spearman,median_spearman,top5_excess_avg,top5_close_avg,
                top5_positive_excess_rate,note,created_at,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.get('model'),s.get('days_requested'),s.get('days_validated'),s.get('symbols_requested'),
                 s.get('mean_spearman'),s.get('median_spearman'),s.get('top5_excess_avg'),
                 s.get('top5_close_avg'),s.get('top5_positive_excess_rate'),s.get('note'),
                 s.get('created_at'),json.dumps(result,ensure_ascii=False)))
            return int(cur.lastrowid)

    def runs(self,limit=20):
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute(
                'SELECT id,model,days_requested,days_validated,symbols_requested,mean_spearman,median_spearman,top5_excess_avg,top5_close_avg,top5_positive_excess_rate,note,created_at FROM runs ORDER BY id DESC LIMIT ?',
                (limit,)).fetchall()]

    def result(self,run_id:int):
        with sqlite3.connect(self.path) as c:
            r=c.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()
            return json.loads(r[0]) if r else None

class HistoricalValidator:
    def __init__(self,kiwoom,live_db_path):
        self.k=kiwoom
        self.store=ValidationStore(live_db_path)

    def daily_history(self,symbol,exchange,calendar_days=260):
        start=(datetime.now(timezone.utc)-timedelta(days=calendar_days)).strftime('%Y%m%d')
        r=requests.post(self.k.s.rest_base+'/api/us/chart',headers=self.k.headers('usa06012'),json={
            'stex_tp':exchange,'stk_cd':symbol,'strt_dt':start,
            'upd_stkpc_tp':'1','exrt_appl_tp':'0'
        },timeout=25)
        d=r.json()
        if d.get('return_code')!=0:
            raise RuntimeError(f"daily {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
        rows=[]
        for x in d.get('result_list') or []:
            dt=str(x.get('dt') or '')
            op=abs(num(x.get('open_pric'))); close=abs(num(x.get('cur_prc')))
            hi=abs(num(x.get('high_pric'))); lo=abs(num(x.get('low_pric')))
            vol=abs(num(x.get('acc_trde_qty')))
            if len(dt)>=8 and op>0 and close>0:
                rows.append({'date':dt[:8],'open':op,'high':hi or max(op,close),
                             'low':lo or min(op,close),'close':close,'volume':vol})
        by={r['date']:r for r in rows}
        return [by[d] for d in sorted(by)]

    def run(self,symbols,days=60):
        hist={}; exchanges={}
        for sym in symbols:
            ex=self.k.active_exchange(sym); exchanges[sym]=ex
            try: hist[sym]=self.daily_history(sym,ex,max(220,int(days*3.0)))
            except Exception: hist[sym]=[]

        qrows=hist.get('QQQ',[])
        if len(qrows)<10: raise RuntimeError('QQQ historical data unavailable')
        srows=hist.get('SMH',[])
        qmap={r['date']:i for i,r in enumerate(qrows)}
        smap={r['date']:i for i,r in enumerate(srows)}
        dates=sorted(qmap.keys())[-days:]
        all_rows=[]; daily=[]

        for day in dates:
            qi=qmap[day]
            if qi<5: continue
            qcur=qrows[qi]; qprev=qrows[qi-1]
            qgap=(qcur['open']/qprev['close']-1)*100
            qret=(qcur['close']/qcur['open']-1)*100

            sgap=sret=0.0
            si=smap.get(day)
            if si is not None and si>=1:
                scur=srows[si]; sprev=srows[si-1]
                sgap=(scur['open']/sprev['close']-1)*100
                sret=(scur['close']/scur['open']-1)*100

            rows=[]
            for sym,h in hist.items():
                if sym in ('QQQ','SMH'): continue
                imap={r['date']:i for i,r in enumerate(h)}
                idx=imap.get(day)
                if idx is None or idx<5: continue
                cur=h[idx]; prev=h[idx-1]; prev5=h[idx-5:idx]
                gap=(cur['open']/prev['close']-1)*100
                f=open_model_score(cur['open'],prev5,gap,qgap,sgap,sym)
                ret=(cur['close']/cur['open']-1)*100
                mfe=(cur['high']/cur['open']-1)*100
                mae=(cur['low']/cur['open']-1)*100
                bench=sret if sym in SEMI and si is not None else qret
                rows.append({'trade_date':day,'symbol':sym,'exchange':exchanges.get(sym,''),
                             'model':'OPEN_V0','score':f['score'],'gap_pct':gap,
                             'ma5':f['ma5'],'ma5_slope_pct':f['ma5_slope_pct'],
                             'atr5_pct':f['atr5_pct'],'avg5_dollar_volume':f['avg5_dollar_volume'],
                             'open_to_close_pct':ret,'mfe_pct':mfe,'mae_pct':mae,
                             'benchmark_pct':bench,'excess_pct':ret-bench})

            if len(rows)<3: continue
            rows.sort(key=lambda r:(r['score'],r['avg5_dollar_volume']),reverse=True)
            for n,r in enumerate(rows,1): r['pred_rank']=n
            actual=sorted(rows,key=lambda r:r['excess_pct'],reverse=True)
            actual_rank={r['symbol']:n for n,r in enumerate(actual,1)}
            for r in rows:r['actual_rank']=actual_rank[r['symbol']]

            pred=pd.Series([r['pred_rank'] for r in rows],dtype=float)
            act=pd.Series([r['actual_rank'] for r in rows],dtype=float)
            rho=pred.corr(act,method='pearson')
            rho=None if pd.isna(rho) else float(rho)
            top5=rows[:5]
            daily.append({'trade_date':day,'spearman':rho,
                          'top5_excess_avg':mean([r['excess_pct'] for r in top5]),
                          'top5_close_avg':mean([r['open_to_close_pct'] for r in top5]),
                          'top5_positive_excess_rate':mean([1 if r['excess_pct']>0 else 0 for r in top5])*100,
                          'universe':len(rows),'qqq_return':qret,'smh_return':sret})
            all_rows.extend(rows)

        if not daily: raise RuntimeError('No historical validation dates could be constructed')
        rhos=[x['spearman'] for x in daily if x['spearman'] is not None]
        summary={'model':'OPEN_V0','days_requested':days,'days_validated':len(daily),
                 'symbols_requested':len(symbols),'mean_spearman':mean(rhos) if rhos else None,
                 'median_spearman':float(pd.Series(rhos).median()) if rhos else None,
                 'top5_excess_avg':mean([x['top5_excess_avg'] for x in daily]),
                 'top5_close_avg':mean([x['top5_close_avg'] for x in daily]),
                 'top5_positive_excess_rate':mean([x['top5_positive_excess_rate'] for x in daily]),
                 'created_at':datetime.now(timezone.utc).isoformat(),
                 'note':'OPEN_V0 uses previous completed daily bars plus the current-day opening print only. Current-day high/low/close/volume are evaluation-only.'}
        result={'summary':summary,'daily':daily,'rows':all_rows}
        summary['run_id']=self.store.save(result)
        return result
