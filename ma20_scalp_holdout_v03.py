#!/usr/bin/env python3
"""MA20_SCALP v0.3 - unseen holdout validation.

Uses ONLY dates outside the v0.2 sweep window 20260722..20260814.
Prefers later dates (>20260814). If unavailable, uses earlier dates (<20260722)
and labels them BACKWARD_HOLDOUT (not forward temporal OOS).

Frozen candidates only; no parameter sweep:
CONTROL    GAP=2.00 RECOVERY=0.25 STOP_EXTRA=0.75
CHALLENGER GAP=1.75 RECOVERY=0.33 STOP_EXTRA=0.75
Costs: 0.20 / 0.25 / 0.30 percent.
Read only. No DB writes. No auto orders.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']
SEEN_MIN='20260722'; SEEN_MAX='20260814'
MAX_HOLD=30
CONFIGS=[('CONTROL',2.00,0.25,0.75),('CHALLENGER',1.75,0.33,0.75)]
COSTS=[0.20,0.25,0.30]


def load():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,et_time,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR'
            AND symbol IN ({marks})
          ORDER BY symbol,trade_date,et_time'''
    df=pd.read_sql_query(q,con,params=SYMS)
    con.close()
    return df


def prep(z):
    c=z.close.to_numpy(float); h=z.high.to_numpy(float)
    ma=pd.Series(c).rolling(20,min_periods=20).mean().to_numpy()
    gap=(ma-c)/ma*100.0
    return c,h,ma,gap,z.et_time.astype(str).to_numpy()


def run_session(sym,date,z,gap_thr,recovery,stop_extra):
    c,h,ma,gap,t=prep(z)
    out=[]; i=21; n=len(c)
    while i<n-1:
        if np.isnan(ma[i]): i+=1; continue
        px=c[i]; g=gap[i]
        target=px+(ma[i]-px)*recovery
        tg=(target/px-1.0)*100.0
        if g>=gap_thr and g<gap[i-1] and tg>=0.50:
            eg=g; end=min(n-1,i+MAX_HOLD); done=False
            for j in range(i+1,end+1):
                if h[j]>=target:
                    gross=(target/px-1.0)*100.0
                    out.append((sym,str(date),t[i],t[j],gross,'TARGET'))
                    i=j+1; done=True; break
                if gap[j]>=eg+stop_extra:
                    gross=(c[j]/px-1.0)*100.0
                    out.append((sym,str(date),t[i],t[j],gross,'GAP_STOP'))
                    i=j+1; done=True; break
            if not done:
                gross=(c[end]/px-1.0)*100.0
                out.append((sym,str(date),t[i],t[end],gross,'TIME'))
                i=end+1
        else:
            i+=1
    return out


def summarize(name,trades,cost):
    if not trades:
        return dict(NAME=name,COST=cost,TRADES=0,NET=0,AVG=0,WIN_RATE=0,PF=0,POS_DATES=0,DATES=0,WORST_DATE=0)
    x=pd.DataFrame(trades,columns=['symbol','date','entry_time','exit_time','gross','reason'])
    x['net']=x.gross-cost
    pos=x.loc[x.net>0,'net'].sum(); neg=-x.loc[x.net<0,'net'].sum()
    pf=pos/neg if neg>0 else float('inf')
    bd=x.groupby('date').net.sum()
    return dict(NAME=name,COST=cost,TRADES=len(x),NET=x.net.sum(),AVG=x.net.mean(),WIN_RATE=(x.net>0).mean()*100,PF=pf,POS_DATES=int((bd>0).sum()),DATES=len(bd),WORST_DATE=bd.min())


def main():
    df=load()
    if df.empty:
        print('NO_DATA'); return
    later=df[df.trade_date>SEEN_MAX].copy()
    earlier=df[df.trade_date<SEEN_MIN].copy()
    if not later.empty:
        use=later; mode='FORWARD_OOS'
    elif not earlier.empty:
        use=earlier; mode='BACKWARD_HOLDOUT'
    else:
        print('NO_UNSEEN_DATES_OUTSIDE',SEEN_MIN,SEEN_MAX); return
    sessions=[(s,d,z.reset_index(drop=True)) for (s,d),z in use.groupby(['symbol','trade_date'],sort=True) if len(z)>=40]
    dates=sorted({str(d) for _,d,_ in sessions})
    print('===== MA20 SCALP v0.3 UNSEEN HOLDOUT =====')
    print('MODE',mode)
    print('SEEN_WINDOW',SEEN_MIN,SEEN_MAX)
    print('HOLDOUT_DATES',len(dates),dates[0],dates[-1])
    print('SESSIONS',len(sessions))
    rows=[]
    for name,gap,recovery,stop in CONFIGS:
        tt=[]
        for sym,date,z in sessions:
            tt += run_session(sym,date,z,gap,recovery,stop)
        for cost in COSTS:
            rows.append(summarize(name,tt,cost))
    r=pd.DataFrame(rows)
    print('\n===== RESULTS =====')
    print(r.round(3).to_string(index=False))
    print('\n===== DECISION =====')
    c=r[(r.NAME=='CONTROL')&(r.COST==0.20)].iloc[0]
    h=r[(r.NAME=='CHALLENGER')&(r.COST==0.20)].iloc[0]
    print('CONTROL_POSITIVE',bool(c.NET>0 and c.PF>1))
    print('CHALLENGER_POSITIVE',bool(h.NET>0 and h.PF>1))
    print('CONTROL_SAMPLE_OK',bool(c.TRADES>=20))
    print('CHALLENGER_SAMPLE_OK',bool(h.TRADES>=20))
    if mode!='FORWARD_OOS':
        print('WARNING: backward holdout is unseen but is NOT forward temporal OOS.')
    print('RULE: do not tune on this holdout. Record result, then move to a new future window if sample is insufficient.')

if __name__=='__main__':
    main()
