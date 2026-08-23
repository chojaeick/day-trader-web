#!/usr/bin/env python3
"""FUJIMOTO v0.3.1 coverage diagnostic.

Purpose:
- Explain why frozen Fujimoto v0.3 produced only 3 cycles across 15 symbols.
- Do NOT change strategy rules or thresholds.
- Summarize available daily coverage by symbol and raw Stage1 precursor counts.
- Identify whether the bottleneck is data coverage versus strategy selectivity.

DB read-only. No auto orders.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
MIN_DAYS=100


def rsi14(c):
    s=pd.Series(c,dtype=float); delta=s.diff()
    up=delta.clip(lower=0); dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs)); return r.where(ad!=0,100.0)


def load_daily_all():
    con=sqlite3.connect(DB)
    q='''SELECT symbol,trade_date,open,high,low,close,volume
         FROM historical_minute_bars
         WHERE interval_min=1 AND session='REGULAR'
         ORDER BY symbol,trade_date,et_time'''
    x=pd.read_sql_query(q,con)
    con.close()
    out={}
    for s,z in x.groupby('symbol',sort=True):
        d=(z.groupby('trade_date',sort=True)
             .agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
             .reset_index())
        d['trade_date']=d['trade_date'].astype(str)
        out[s]=d
    return out


def precursor_counts(d):
    z=d.copy(); c=z.close.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    rprev=z.rsi.shift(1)
    z['drsi_low']=rprev.rolling(60,min_periods=30).quantile(.20).ewm(span=5,adjust=False).mean()
    pl=(l.shift(2)<l.shift(4))&(l.shift(2)<=l.shift(3))&(l.shift(2)<=l.shift(1))&(l.shift(2)<l)
    z['pivot']=pl.fillna(False)
    z['pivot_price']=np.where(z.pivot,l.shift(2),np.nan)
    z['pivot_rsi']=np.where(z.pivot,z.rsi.shift(2),np.nan)
    last_p=last_r=prev_p=prev_r=np.nan; bull=[]
    for _,row in z.iterrows():
        flag=False
        if bool(row.pivot) and np.isfinite(row.pivot_price) and np.isfinite(row.pivot_rsi):
            prev_p,prev_r=last_p,last_r; last_p,last_r=float(row.pivot_price),float(row.pivot_rsi)
            if np.isfinite(prev_p) and np.isfinite(prev_r):
                flag=(last_p<prev_p) and (last_r>prev_r)
        bull.append(flag)
    z['bull_div']=bull
    z['drsi_cross']=(z.rsi.shift(1)<=z.drsi_low.shift(1))&(z.rsi>z.drsi_low)

    div_idx=np.flatnonzero(z.bull_div.to_numpy(bool)); cross_idx=np.flatnonzero(z.drsi_cross.fillna(False).to_numpy(bool))
    latch_hits=0
    for i in div_idx:
        if np.any((cross_idx>i)&(cross_idx<=i+10)):
            latch_hits+=1
    return int(z.rsi.notna().sum()),int(z.drsi_low.notna().sum()),int(z.pivot.sum()),int(z.bull_div.sum()),int(z.drsi_cross.sum()),latch_hits


def main():
    daily=load_daily_all()
    rows=[]
    for s,d in daily.items():
        n=len(d)
        first=str(d.trade_date.iloc[0]) if n else ''
        last=str(d.trade_date.iloc[-1]) if n else ''
        rsi_ok,drsi_ok,piv,div,cross,hit=precursor_counts(d) if n else (0,0,0,0,0,0)
        rows.append((s,n,first,last,rsi_ok,drsi_ok,piv,div,cross,hit,n>=MIN_DAYS))
    r=pd.DataFrame(rows,columns=['SYMBOL','DAYS','FIRST','LAST','RSI_VALID','DRSI_VALID','PIVOTS','BULL_DIV','DRSI_CROSS','DIV_TO_CROSS_10D','ELIGIBLE'])
    print('===== FUJIMOTO v0.3.1 COVERAGE DIAGNOSTIC =====')
    print('RULES UNCHANGED / DB READ ONLY')
    print('TOTAL_SYMBOLS_IN_DB',len(r))
    print('ELIGIBLE_GE_100_DAYS',int(r.ELIGIBLE.sum()))
    print('\n===== ELIGIBLE SYMBOLS =====')
    z=r[r.ELIGIBLE].sort_values(['DAYS','SYMBOL'],ascending=[False,True])
    print(z.to_string(index=False))
    print('\n===== INELIGIBLE / THIN COVERAGE =====')
    q=r[~r.ELIGIBLE].sort_values(['DAYS','SYMBOL'],ascending=[False,True])
    print(q[['SYMBOL','DAYS','FIRST','LAST','BULL_DIV','DRSI_CROSS','DIV_TO_CROSS_10D']].to_string(index=False))
    print('\n===== TOTAL PRECURSORS ON ELIGIBLE =====')
    print('DAYS',int(z.DAYS.sum()))
    print('PIVOTS',int(z.PIVOTS.sum()))
    print('BULL_DIV',int(z.BULL_DIV.sum()))
    print('DRSI_CROSS',int(z.DRSI_CROSS.sum()))
    print('DIV_TO_CROSS_10D',int(z.DIV_TO_CROSS_10D.sum()))
    print('\nINTERPRETATION: if eligible coverage is only ~135 days per symbol and DIV_TO_CROSS_10D is tiny, the next action is more historical coverage/universe, not threshold tuning.')

if __name__=='__main__': main()
