#!/usr/bin/env python3
"""TOM DEMARK EXTREME EXHAUSTION v0.3

Purpose
- Build a new architecture from the v0.2 audit finding that raw TD9 has only a weak edge,
  while the strongest pre-drop quartile had materially larger rebound excursion.
- Keep TD9 definition fixed: 9 consecutive 5m closes below close 4 bars earlier.
- Remove Heikin-Ashi as an entry requirement.
- Filter TD9 by causal pre-drop severity measured over prior 6 bars.
- Test fixed forward exits only (3/6/12 bars) to diagnose whether extreme exhaustion can
  overcome realistic round-trip cost assumptions.
- Discovery/training probe only. DB read only. No auto orders.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
MIN_DAYS=100
COSTS=[0.20,0.25,0.30]
# Coarse architectural buckets, not fine tuning.
DROP_THRESHOLDS=[0.75,1.00,1.25,1.50]
HOLDS=[3,6,12]
TD_N=9


def discover_symbols():
    con=sqlite3.connect(DB)
    q="""SELECT symbol,COUNT(DISTINCT trade_date) days
         FROM historical_minute_bars
         WHERE interval_min=1 AND session='REGULAR'
         GROUP BY symbol HAVING days>=? ORDER BY symbol"""
    z=pd.read_sql_query(q,con,params=[MIN_DAYS]); con.close()
    return z.symbol.astype(str).tolist()


def load_5m(symbols):
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in symbols)
    q=f"""SELECT symbol,trade_date,et_time,open,high,low,close,volume
           FROM historical_minute_bars
           WHERE interval_min=1 AND session='REGULAR' AND symbol IN ({marks})
           ORDER BY symbol,trade_date,et_time"""
    x=pd.read_sql_query(q,con,params=symbols); con.close()
    out=[]
    for (s,d),z in x.groupby(['symbol','trade_date'],sort=True):
        if len(z)<60: continue
        z=z.reset_index(drop=True).copy(); z['bucket']=np.arange(len(z))//5
        b=(z.groupby('bucket',sort=True)
            .agg(time=('et_time','last'),open=('open','first'),high=('high','max'),
                 low=('low','min'),close=('close','last'),volume=('volume','sum'))
            .reset_index(drop=True))
        if len(b)>=25: out.append((str(s),str(d),b))
    return out


def td9_events(sym,day,b):
    c=b.close.to_numpy(float); h=b.high.to_numpy(float); l=b.low.to_numpy(float)
    n=len(c); cond=np.zeros(n,bool)
    if n>4: cond[4:]=c[4:]<c[:-4]
    k=0; rows=[]
    for i in range(n):
        k=k+1 if cond[i] else 0
        if k!=TD_N or i<6: continue
        # prior 6-bar return ending at TD9 bar; negative means decline
        drop6=(c[i]/c[i-6]-1.0)*100.0
        for hold in HOLDS:
            j=min(n-1,i+hold)
            if j<=i: continue
            gross=(c[j]/c[i]-1.0)*100.0
            hi=(np.max(h[i+1:j+1])/c[i]-1.0)*100.0 if j>i else 0.0
            lo=(np.min(l[i+1:j+1])/c[i]-1.0)*100.0 if j>i else 0.0
            rows.append((sym,day,i,hold,drop6,gross,hi,lo))
    return rows


def summary(x,cost):
    if x.empty:
        return dict(TRADES=0,NET=0.,AVG=0.,WIN_RATE=0.,PF=0.,WORST=0.,POS_DATES=0,DATES=0)
    net=x.gross-cost
    pos=net[net>0].sum(); neg=-net[net<0].sum(); bydate=pd.Series(net.values,index=x.date).groupby(level=0).sum()
    return dict(TRADES=len(x),NET=float(net.sum()),AVG=float(net.mean()),WIN_RATE=float((net>0).mean()*100),
                PF=(float(pos/neg) if neg>0 else float('inf')),WORST=float(net.min()),
                POS_DATES=int((bydate>0).sum()),DATES=len(bydate))


def main():
    syms=discover_symbols(); data=load_5m(syms)
    rows=[]
    for s,d,b in data: rows.extend(td9_events(s,d,b))
    e=pd.DataFrame(rows,columns=['symbol','date','idx','hold','drop6','gross','mfe','mae'])
    base=e[e.hold==6]
    print('===== TOM DEMARK EXTREME EXHAUSTION v0.3 =====')
    print('5M / TD9 FIXED / HA ENTRY REMOVED / DB READ ONLY / NO AUTO ORDER')
    print('SYMBOLS',len(syms),'SYMBOL_DAYS',len(data),'TD9_EVENTS',len(base))
    if not base.empty:
        print('DROP6 MEDIAN',f'{base.drop6.median():+.3f}%','Q25',f'{base.drop6.quantile(.25):+.3f}%','MIN',f'{base.drop6.min():+.3f}%')
    print('\n===== EXTREME DROP GRID @ COST0.20 =====')
    out=[]
    for thr in DROP_THRESHOLDS:
        for hold in HOLDS:
            q=e[(e.hold==hold)&(e.drop6<=-thr)].copy()
            m=summary(q,0.20)
            out.append((thr,hold,*m.values(),float(q.mfe.mean()) if len(q) else 0.,float(q.mae.mean()) if len(q) else 0.))
    cols=['DROP_ABS','HOLD','TRADES','NET','AVG','WIN_RATE','PF','WORST','POS_DATES','DATES','MFE_AVG','MAE_AVG']
    r=pd.DataFrame(out,columns=cols)
    print(r.round(3).sort_values(['NET','PF'],ascending=False).to_string(index=False))

    print('\n===== COST ROBUSTNESS FOR POSITIVE COST0.20 CASES =====')
    pos20=r[(r.TRADES>=20)&(r.NET>0)&(r.PF>1)].sort_values('NET',ascending=False)
    if pos20.empty:
        print('NONE')
    else:
        for _,row in pos20.iterrows():
            thr=float(row.DROP_ABS); hold=int(row.HOLD)
            q=e[(e.hold==hold)&(e.drop6<=-thr)].copy()
            print(f'-- DROP>={thr:.2f}% HOLD={hold} --')
            for cost in COSTS:
                m=summary(q,cost)
                print('COST',f'{cost:.2f}','TRADES',m['TRADES'],'NET',f"{m['NET']:+.3f}%",'AVG',f"{m['AVG']:+.3f}%",'WIN',f"{m['WIN_RATE']:.1f}%",'PF',('inf' if np.isinf(m['PF']) else f"{m['PF']:.3f}"))

    print('\n===== DECISION SUPPORT =====')
    survivors=[]
    for thr in DROP_THRESHOLDS:
        for hold in HOLDS:
            q=e[(e.hold==hold)&(e.drop6<=-thr)].copy()
            ms=[summary(q,c) for c in COSTS]
            if ms[0]['TRADES']>=20 and all(m['NET']>0 and m['PF']>1 for m in ms):
                survivors.append((thr,hold,ms[0]['TRADES'],min(m['NET'] for m in ms),min(m['PF'] for m in ms)))
    print('SURVIVORS_ALL_COSTS',len(survivors))
    for s in survivors:
        print('DROP',s[0],'HOLD',s[1],'TRADES',s[2],'MIN_NET',f'{s[3]:+.3f}%','MIN_PF',f'{s[4]:.3f}')
    print('RULE: do not fine-tune this sample. Any survivor must be frozen and sent to temporal holdout.')

if __name__=='__main__': main()
