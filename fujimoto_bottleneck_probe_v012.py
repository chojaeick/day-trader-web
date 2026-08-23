#!/usr/bin/env python3
"""FUJIMOTO v0.1.2 - bottleneck architecture probe.

Purpose
- Keep v0.1 indicator definitions unchanged.
- Diagnose whether signal sparsity comes from exact same-day conjunctions.
- Do NOT tune thresholds.
- Compare causal sequencing windows for Stage1/2/3.
- DB read-only, no auto orders.

Key idea:
The v0.1 audit shows components often exist individually, but almost never align on the
same bar. This probe tests stateful sequencing only:
  Stage1: bullish divergence confirmed, then DRSI lower cross within N sessions.
  Stage2: after Stage1, RSI > dynamic mid AND MACD histogram improving within N sessions.
  Stage3: after Stage2, cloud breakout AND Chikou bullish AND MACD bullish within N sessions.
No thresholds are changed; only the architecture is made explicitly stateful/causal.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']
STAGE1_WINDOWS=[10,20,30]
STAGE2_WINDOWS=[10,20,30]
STAGE3_WINDOWS=[10,20,30]


def load_daily():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,et_time,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR'
            AND symbol IN ({marks})
          ORDER BY symbol,trade_date,et_time'''
    x=pd.read_sql_query(q,con,params=SYMS)
    con.close()
    out={}
    for s,z in x.groupby('symbol',sort=True):
        d=(z.groupby('trade_date',sort=True)
             .agg(open=('open','first'),high=('high','max'),low=('low','min'),
                  close=('close','last'),volume=('volume','sum'))
             .reset_index())
        d['trade_date']=d['trade_date'].astype(str)
        out[s]=d
    return out


def rsi14(c):
    s=pd.Series(c,dtype=float)
    delta=s.diff(); up=delta.clip(lower=0); dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs)); r=r.where(ad!=0,100.0)
    return r


def add_indicators(d):
    z=d.copy(); c=z.close.astype(float); h=z.high.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    rprev=z.rsi.shift(1)
    z['drsi_low']=rprev.rolling(60,min_periods=30).quantile(.20).ewm(span=5,adjust=False).mean()
    z['drsi_mid']=rprev.rolling(60,min_periods=30).quantile(.50).ewm(span=5,adjust=False).mean()
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    z['macd']=ema12-ema26; z['macd_sig']=z.macd.ewm(span=9,adjust=False).mean(); z['macd_hist']=z.macd-z.macd_sig
    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2
    kijun=(h.rolling(26).max()+l.rolling(26).min())/2
    base_a=(tenkan+kijun)/2; base_b=(h.rolling(52).max()+l.rolling(52).min())/2
    z['cloud_a']=base_a.shift(26); z['cloud_b']=base_b.shift(26)
    z['cloud_top']=z[['cloud_a','cloud_b']].max(axis=1); z['cloud_bot']=z[['cloud_a','cloud_b']].min(axis=1)
    z['chikou_bull']=c>c.shift(26)
    pl=(l.shift(2)<l.shift(4))&(l.shift(2)<=l.shift(3))&(l.shift(2)<=l.shift(1))&(l.shift(2)<l)
    z['pivot_low_confirmed']=pl.fillna(False)
    z['pivot_price']=np.where(z.pivot_low_confirmed,l.shift(2),np.nan)
    z['pivot_rsi']=np.where(z.pivot_low_confirmed,z.rsi.shift(2),np.nan)
    last_p=last_r=prev_p=prev_r=np.nan; bull=[]
    for _,row in z.iterrows():
        flag=False
        if bool(row.pivot_low_confirmed) and np.isfinite(row.pivot_price) and np.isfinite(row.pivot_rsi):
            prev_p,prev_r=last_p,last_r; last_p,last_r=float(row.pivot_price),float(row.pivot_rsi)
            if np.isfinite(prev_p) and np.isfinite(prev_r): flag=(last_p<prev_p) and (last_r>prev_r)
        bull.append(flag)
    z['bull_div']=bull
    z['rsi_cross_up_low']=(z.rsi.shift(1)<=z.drsi_low.shift(1))&(z.rsi>z.drsi_low)
    z['mid_macd_accel']=(z.rsi>z.drsi_mid)&(z.macd_hist>0)&(z.macd_hist>z.macd_hist.shift(1))
    z['cloud_break_up']=(c.shift(1)<=z.cloud_top.shift(1))&(c>z.cloud_top)
    z['stage3_cond']=z.cloud_break_up&z.chikou_bull&(z.macd>z.macd_sig)
    return z


def walk(z,w1,w2,w3):
    s1=s2=s3=0; div_i=None; s1_i=None; s2_i=None
    events=[]
    for i,row in z.iterrows():
        if bool(row.bull_div):
            div_i=i
        if div_i is not None and i-div_i>w1:
            div_i=None
        if s1==0 and div_i is not None and bool(row.rsi_cross_up_low):
            s1=1; s1_i=i; div_i=None; events.append(('S1',str(row.trade_date),i))
        if s1 and not s2:
            if i-s1_i>w2:
                s1=0; s1_i=None
            elif bool(row.mid_macd_accel):
                s2=1; s2_i=i; events.append(('S2',str(row.trade_date),i))
        if s2 and not s3:
            if i-s2_i>w3:
                s1=s2=0; s1_i=s2_i=None
            elif bool(row.stage3_cond):
                s3=1; events.append(('S3',str(row.trade_date),i))
                s1=s2=s3=0; s1_i=s2_i=None
    return events


def main():
    daily=load_daily(); prepared={s:add_indicators(d) for s,d in daily.items()}
    print('===== FUJIMOTO v0.1.2 BOTTLENECK ARCHITECTURE PROBE =====')
    print('INDICATOR THRESHOLDS UNCHANGED / STATEFUL SEQUENCING ONLY / DB READ ONLY')
    rows=[]
    for w1 in STAGE1_WINDOWS:
        for w2 in STAGE2_WINDOWS:
            for w3 in STAGE3_WINDOWS:
                c1=c2=c3=0; sy3=set(); ev=[]
                for s,z in prepared.items():
                    e=walk(z,w1,w2,w3); ev.extend((s,*x) for x in e)
                    c1+=sum(x[0]=='S1' for x in e); c2+=sum(x[0]=='S2' for x in e); c3+=sum(x[0]=='S3' for x in e)
                    if any(x[0]=='S3' for x in e): sy3.add(s)
                rows.append((w1,w2,w3,c1,c2,c3,len(sy3)))
    r=pd.DataFrame(rows,columns=['W1','W2','W3','STAGE1','STAGE2','STAGE3','S3_SYMBOLS'])
    print('\n===== GRID SUMMARY =====')
    print(r.sort_values(['STAGE3','STAGE2','STAGE1'],ascending=False).head(27).to_string(index=False))
    best=r.sort_values(['STAGE3','STAGE2','STAGE1','W1','W2','W3'],ascending=[False,False,False,True,True,True]).iloc[0]
    print('\n===== BEST ARCHITECTURE SUPPORT =====')
    print(best.to_string())
    bw1,bw2,bw3=map(int,[best.W1,best.W2,best.W3])
    print('\n===== EVENTS FOR BEST =====')
    for s,z in prepared.items():
        e=walk(z,bw1,bw2,bw3)
        if e:
            print(s, e)
    print('\nINTERPRETATION: if Stage3 remains near zero even with causal sequencing, inspect the Stage3 cloud/chikou architecture next. Do not micro-tune indicator thresholds on this sample.')

if __name__=='__main__': main()
