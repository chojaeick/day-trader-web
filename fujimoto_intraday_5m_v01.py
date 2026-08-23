#!/usr/bin/env python3
"""FUJIMOTO_INTRADAY_5M v0.1

Purpose
- Adapt the frozen Fujimoto staged architecture to intraday 5-minute bars.
- Aggregate existing REGULAR 1m DB rows to causal 5m bars.
- Keep staged entry logic: 10% / +20% / +70%.
- Reset state each trading day and force flat at EOD (day-trading only).
- Cost stress: 0.20 / 0.25 / 0.30% round-trip assumptions.
- DB read-only. No auto orders.

Important
This is an intraday adaptation screen, not evidence that the original Fujimoto method
was defined for 5-minute bars. We are testing whether the same architecture survives
shorter timeframes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SPY','TSM']
COSTS=[0.20,0.25,0.30]
W1,W2,W3=10,20,20  # 5m bars: 50m / 100m / 100m


def rsi14(c):
    s=pd.Series(c,dtype=float); d=s.diff()
    up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs))
    return r.where(ad!=0,100.0)


def load_5m():
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
    for (s,d),z in x.groupby(['symbol','trade_date'],sort=True):
        if len(z)<50: continue
        z=z.reset_index(drop=True).copy()
        z['bucket']=np.arange(len(z))//5
        b=(z.groupby('bucket',sort=True)
             .agg(time=('et_time','first'),open=('open','first'),high=('high','max'),
                  low=('low','min'),close=('close','last'),volume=('volume','sum'))
             .reset_index(drop=True))
        b['trade_date']=str(d); b['symbol']=s
        out.setdefault(s,[]).append(b)
    return {s:pd.concat(parts,ignore_index=True) for s,parts in out.items() if parts}


def add_indicators(z):
    z=z.copy(); c=z.close.astype(float); h=z.high.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    prev=z.rsi.shift(1)
    z['drsi_low']=prev.rolling(60,min_periods=30).quantile(.20).ewm(span=5,adjust=False).mean()
    z['drsi_mid']=prev.rolling(60,min_periods=30).quantile(.50).ewm(span=5,adjust=False).mean()
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    z['macd']=ema12-ema26; z['macd_sig']=z.macd.ewm(span=9,adjust=False).mean(); z['macd_hist']=z.macd-z.macd_sig
    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2
    kijun=(h.rolling(26).max()+l.rolling(26).min())/2
    a=((tenkan+kijun)/2).shift(26); b=((h.rolling(52).max()+l.rolling(52).min())/2).shift(26)
    z['cloud_top']=pd.concat([a,b],axis=1).max(axis=1)
    z['cloud_bot']=pd.concat([a,b],axis=1).min(axis=1)
    z['chikou_bull']=c>c.shift(26)

    pl=(l.shift(2)<l.shift(4))&(l.shift(2)<=l.shift(3))&(l.shift(2)<=l.shift(1))&(l.shift(2)<l)
    z['pivot_low_confirmed']=pl.fillna(False)
    z['pivot_price']=np.where(z.pivot_low_confirmed,l.shift(2),np.nan)
    z['pivot_rsi']=np.where(z.pivot_low_confirmed,z.rsi.shift(2),np.nan)

    last_p=last_r=prev_p=prev_r=np.nan; bull=[]
    last_day=None
    for _,row in z.iterrows():
        day=str(row.trade_date)
        if day!=last_day:
            last_p=last_r=prev_p=prev_r=np.nan
            last_day=day
        flag=False
        if bool(row.pivot_low_confirmed) and np.isfinite(row.pivot_price) and np.isfinite(row.pivot_rsi):
            prev_p,prev_r=last_p,last_r
            last_p,last_r=float(row.pivot_price),float(row.pivot_rsi)
            if np.isfinite(prev_p) and np.isfinite(prev_r):
                flag=(last_p<prev_p) and (last_r>prev_r)
        bull.append(flag)
    z['bull_div']=bull
    z['rsi_cross_up_low']=(z.rsi.shift(1)<=z.drsi_low.shift(1))&(z.rsi>z.drsi_low)
    z['rsi_cross_down_mid']=(z.rsi.shift(1)>=z.drsi_mid.shift(1))&(z.rsi<z.drsi_mid)
    z['macd_dead']=(z.macd.shift(1)>=z.macd_sig.shift(1))&(z.macd<z.macd_sig)
    z['macd_bull']=z.macd>z.macd_sig
    z['cloud_break_up']=(c.shift(1)<=z.cloud_top.shift(1))&(c>z.cloud_top)
    z['cloud_break_down']=(c.shift(1)>=z.cloud_bot.shift(1))&(c<z.cloud_bot)
    return z


def run_symbol(sym,z):
    raw_cycles=[]; event_counts={'S1':0,'S2':0,'S3':0,'E1':0,'E2':0,'E3':0,'EOD':0}
    day_groups=list(z.groupby('trade_date',sort=True))
    for day,g in day_groups:
        g=g.reset_index(drop=True)
        pos=0.0; avg=0.0; state=0; deadline=None; cycle=[]

        def buy(frac,px,reason):
            nonlocal pos,avg
            new=pos+frac; avg=(avg*pos+px*frac)/new if new>0 else 0.0; pos=new
            event_counts[reason]+=1

        def sell(frac,px,reason):
            nonlocal pos,avg
            frac=min(frac,pos)
            if frac<=0:return
            gross=((px/avg)-1)*100.0*frac if avg>0 else 0.0
            cycle.append((gross,frac,reason))
            pos-=frac; event_counts[reason]+=1
            if pos<=1e-12:
                pos=0.0; avg=0.0

        for i,row in g.iterrows():
            px=float(row.close)
            if state==0 and bool(row.bull_div):
                state=10; deadline=i+W1
            elif state==10:
                if i>deadline: state=0; deadline=None
                elif bool(row.rsi_cross_up_low):
                    buy(.10,px,'S1'); state=20; deadline=i+W2
            elif state==20:
                if i>deadline: state=0; deadline=None
                elif np.isfinite(row.drsi_mid) and row.rsi>row.drsi_mid and row.macd_hist>0 and i>0 and row.macd_hist>g.iloc[i-1].macd_hist:
                    buy(.20,px,'S2'); state=30; deadline=i+W3
            elif state==30:
                if i>deadline: state=0; deadline=None
                elif bool(row.cloud_break_up) and bool(row.chikou_bull) and bool(row.macd_bull):
                    buy(.70,px,'S3'); state=40; deadline=None

            if pos>0:
                if bool(row.macd_dead): sell(.10,px,'E1')
                elif bool(row.rsi_cross_down_mid): sell(.20,px,'E2')
                elif bool(row.cloud_break_down):
                    sell(pos,px,'E3'); state=0; deadline=None

        if pos>0:
            sell(pos,float(g.iloc[-1].close),'EOD')
        if cycle:
            raw_cycles.append((sym,str(day),cycle))
    return raw_cycles,event_counts


def summarize(raw,cost):
    vals=[]
    for sym,day,legs in raw:
        gross=sum(x[0] for x in legs)
        used=sum(x[1] for x in legs)
        net=gross-cost*used
        vals.append((sym,day,net,gross,used))
    if not vals:
        return dict(CYCLES=0,NET=0.,AVG=0.,WIN_RATE=0.,PF=0.,WORST=0.,POS_DATES=0,DATES=0)
    x=pd.DataFrame(vals,columns=['symbol','date','net','gross','used'])
    pos=x.loc[x.net>0,'net'].sum(); neg=-x.loc[x.net<0,'net'].sum()
    bydate=x.groupby('date').net.sum()
    return dict(CYCLES=len(x),NET=x.net.sum(),AVG=x.net.mean(),WIN_RATE=(x.net>0).mean()*100,
                PF=(pos/neg if neg>0 else float('inf')),WORST=x.net.min(),
                POS_DATES=int((bydate>0).sum()),DATES=len(bydate))


def main():
    data=load_5m(); all_raw=[]; totals={k:0 for k in ['S1','S2','S3','E1','E2','E3','EOD']}
    print('===== FUJIMOTO_INTRADAY_5M v0.1 =====')
    print('5M FROM REGULAR 1M / STATE RESET DAILY / FORCE FLAT EOD / DB READ ONLY')
    print('SYMBOLS',len(data),','.join(sorted(data)))
    total_bars=0; total_days=0
    for s,z0 in data.items():
        z=add_indicators(z0); total_bars+=len(z); total_days+=z.trade_date.nunique()
        raw,ev=run_symbol(s,z); all_raw.extend(raw)
        for k,v in ev.items(): totals[k]+=v
    print('TOTAL_5M_BARS',total_bars,'SYMBOL_DAYS',total_days)
    print('EVENTS',' '.join(f'{k}={v}' for k,v in totals.items()))
    print('\n===== COST SWEEP =====')
    rows=[]
    for c in COSTS:
        m=summarize(all_raw,c); rows.append((c,*m.values()))
    r=pd.DataFrame(rows,columns=['COST','CYCLES','NET','AVG','WIN_RATE','PF','WORST','POS_DATES','DATES'])
    print(r.round(3).to_string(index=False))
    print('\nDECISION_SUPPORT')
    print('SAMPLE_OK',len(all_raw)>=20)
    print('STAGE3_PRESENT',totals['S3']>0)
    print('BASE_COST_POSITIVE',summarize(all_raw,0.20)['NET']>0)
    print('COST30_POSITIVE',summarize(all_raw,0.30)['NET']>0)
    print('NEXT: if signal count is adequate, freeze this 5m architecture and split temporal OOS. If too sparse, audit Stage1/Stage3 bottlenecks before tuning.')

if __name__=='__main__': main()
