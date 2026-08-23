#!/usr/bin/env python3
"""FUJIMOTO v0.2 - freeze sequential architecture and run staged PnL backtest.

Architecture frozen from v0.1.2 support probe:
- W1=10 sessions from bullish divergence to Dynamic RSI lower cross -> Stage1 10%
- W2=20 sessions from Stage1 to RSI > Dynamic Mid + MACD histogram positive/improving -> Stage2 +20%
- W3=20 sessions from Stage2 to cloud breakout + causal Chikou bullish + MACD bullish -> Stage3 +70%

Exit staging:
- 10% on MACD dead cross
- 20% on Dynamic RSI mid cross down
- remainder on cloud breakdown
- residual mark-to-market at sample end

Daily bars are aggregated from existing REGULAR 1m data. DB read-only. No auto orders.
This is an architecture screening backtest, not promotion evidence.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']
COST_RT=0.20
W1,W2,W3=10,20,20


def load_daily():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR' AND symbol IN ({marks})
          ORDER BY symbol,trade_date,et_time'''
    x=pd.read_sql_query(q,con,params=SYMS)
    con.close()
    out={}
    for s,z in x.groupby('symbol',sort=True):
        d=(z.groupby('trade_date',sort=True)
             .agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
             .reset_index())
        d['trade_date']=d['trade_date'].astype(str)
        out[s]=d
    return out


def rsi14(c):
    s=pd.Series(c,dtype=float); delta=s.diff()
    up=delta.clip(lower=0); dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs)); r=r.where(ad!=0,100.0)
    return r


def indicators(d):
    z=d.copy(); c=z.close.astype(float); h=z.high.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    rprev=z['rsi'].shift(1)
    z['drsi_low']=rprev.rolling(60,min_periods=30).quantile(.20).ewm(span=5,adjust=False).mean()
    z['drsi_mid']=rprev.rolling(60,min_periods=30).quantile(.50).ewm(span=5,adjust=False).mean()
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    z['macd']=ema12-ema26; z['macd_sig']=z.macd.ewm(span=9,adjust=False).mean(); z['macd_hist']=z.macd-z.macd_sig
    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2
    kijun=(h.rolling(26).max()+l.rolling(26).min())/2
    a=((tenkan+kijun)/2).shift(26); b=((h.rolling(52).max()+l.rolling(52).min())/2).shift(26)
    z['cloud_top']=pd.concat([a,b],axis=1).max(axis=1); z['cloud_bot']=pd.concat([a,b],axis=1).min(axis=1)
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
    z['rsi_cross_down_mid']=(z.rsi.shift(1)>=z.drsi_mid.shift(1))&(z.rsi<z.drsi_mid)
    z['macd_dead']=(z.macd.shift(1)>=z.macd_sig.shift(1))&(z.macd<z.macd_sig)
    z['macd_bull']=z.macd>z.macd_sig
    z['cloud_break_up']=(c.shift(1)<=z.cloud_top.shift(1))&(c>z.cloud_top)
    z['cloud_break_down']=(c.shift(1)>=z.cloud_bot.shift(1))&(c<z.cloud_bot)
    return z


def run_symbol(sym,z):
    pos=0.0; avg=0.0; net=0.0; events=[]
    state=0; deadline=None
    cycle_net=0.0; cycles=[]

    def buy(frac,px,date,reason):
        nonlocal pos,avg
        new=pos+frac; avg=(avg*pos+px*frac)/new if new>0 else 0.0; pos=new
        events.append((sym,date,'BUY',frac,px,reason))

    def sell(frac,px,date,reason):
        nonlocal pos,avg,net,cycle_net
        frac=min(frac,pos)
        if frac<=0:return
        pnl=((px/avg)-1)*100.0*frac - COST_RT*frac if avg>0 else 0.0
        net+=pnl; cycle_net+=pnl; pos-=frac
        events.append((sym,date,'SELL',frac,px,reason,pnl))
        if pos<=1e-12:
            pos=0.0; avg=0.0; cycles.append(cycle_net); cycle_net=0.0

    for i,row in z.iterrows():
        px=float(row.close); date=str(row.trade_date)
        if state==0 and bool(row.bull_div): state=10; deadline=i+W1
        elif state==10:
            if i>deadline: state=0; deadline=None
            elif bool(row.rsi_cross_up_low): buy(.10,px,date,'STAGE1'); state=20; deadline=i+W2
        elif state==20:
            if i>deadline: state=0; deadline=None
            elif np.isfinite(row.drsi_mid) and row.rsi>row.drsi_mid and row.macd_hist>0 and i>0 and row.macd_hist>z.iloc[i-1].macd_hist:
                buy(.20,px,date,'STAGE2'); state=30; deadline=i+W3
        elif state==30:
            if i>deadline: state=0; deadline=None
            elif bool(row.cloud_break_up) and bool(row.chikou_bull) and bool(row.macd_bull):
                buy(.70,px,date,'STAGE3'); state=40; deadline=None

        if pos>0:
            if bool(row.macd_dead): sell(.10,px,date,'EXIT1_MACD_DEAD')
            elif bool(row.rsi_cross_down_mid): sell(.20,px,date,'EXIT2_DRSI_MID_DOWN')
            elif bool(row.cloud_break_down): sell(pos,px,date,'EXIT3_CLOUD_BREAKDOWN'); state=0; deadline=None

    if pos>0 and len(z): sell(pos,float(z.iloc[-1].close),str(z.iloc[-1].trade_date),'EOD_SAMPLE_CLOSE')
    return net,events,cycles


def main():
    daily=load_daily(); all_events=[]; all_cycles=[]; rows=[]
    print('===== FUJIMOTO v0.2 STAGED PNL BACKTEST =====')
    print('FROZEN WINDOWS',W1,W2,W3,'COST_RT',COST_RT,'DB READ ONLY / NO AUTO ORDER')
    for s,d in daily.items():
        z=indicators(d); net,ev,cy=run_symbol(s,z)
        rows.append((s,len(d),net,sum(e[5]=='STAGE1' for e in ev if e[2]=='BUY'),sum(e[5]=='STAGE2' for e in ev if e[2]=='BUY'),sum(e[5]=='STAGE3' for e in ev if e[2]=='BUY'),len(cy)))
        all_events.extend(ev); all_cycles.extend(cy)
    r=pd.DataFrame(rows,columns=['SYMBOL','DAYS','NET','S1','S2','S3','CYCLES'])
    print('\n===== BY SYMBOL ====='); print(r.round(3).to_string(index=False))
    print('\nTOTAL_NET',f'{r.NET.sum():+.3f}%')
    print('TOTAL_CYCLES',len(all_cycles))
    if all_cycles:
        a=np.array(all_cycles,float); pos=a[a>0].sum(); neg=-a[a<0].sum()
        print('WIN_RATE',f'{(a>0).mean()*100:.1f}%')
        print('PF',('inf' if neg==0 else f'{pos/neg:.3f}'))
        print('AVG_CYCLE',f'{a.mean():+.3f}%')
        print('WORST_CYCLE',f'{a.min():+.3f}%')
    if all_events:
        print('\n===== EVENT COUNTS =====')
        print(pd.Series([e[5] for e in all_events]).value_counts().to_string())
        print('\n===== EVENTS =====')
        for e in all_events: print(e)
    print('\nDECISION_RULE: architecture screen only. Do not promote from this same sample. If event count remains tiny, expand historical universe/coverage before tuning thresholds.')

if __name__=='__main__': main()
