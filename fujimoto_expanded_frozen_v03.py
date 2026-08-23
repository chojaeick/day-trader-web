#!/usr/bin/env python3
"""FUJIMOTO v0.3 - frozen architecture on expanded available DB coverage.

Rules are intentionally unchanged from v0.2:
- W1/W2/W3 = 10/20/20
- Stage1 +10%, Stage2 +20%, Stage3 +70%
- Same Dynamic RSI, MACD, Ichimoku, causal divergence definitions
- Same staged exits and round-trip cost assumption 0.20%

This script changes sample coverage only. It discovers symbols with enough REGULAR 1m
history in the local DB and runs the frozen architecture once per symbol.
DB read-only. No auto orders. No parameter sweep.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
COST_RT=0.20
W1,W2,W3=10,20,20
MIN_DAYS=100


def discover_symbols():
    con=sqlite3.connect(DB)
    q='''SELECT symbol, COUNT(DISTINCT trade_date) AS days
         FROM historical_minute_bars
         WHERE interval_min=1 AND session='REGULAR'
         GROUP BY symbol
         HAVING COUNT(DISTINCT trade_date) >= ?
         ORDER BY symbol'''
    x=pd.read_sql_query(q,con,params=[MIN_DAYS])
    con.close()
    return x


def load_daily(symbols):
    if not symbols: return {}
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in symbols)
    q=f'''SELECT symbol,trade_date,et_time,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR' AND symbol IN ({marks})
          ORDER BY symbol,trade_date,et_time'''
    x=pd.read_sql_query(q,con,params=symbols)
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
    s=pd.Series(c,dtype=float); delta=s.diff()
    up=delta.clip(lower=0); dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs)); return r.where(ad!=0,100.0)


def indicators(d):
    z=d.copy(); c=z.close.astype(float); h=z.high.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    rprev=z.rsi.shift(1)
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
    pos=0.0; avg=0.0; net=0.0; events=[]; state=0; deadline=None
    cycle_net=0.0; cycles=[]
    def buy(frac,px,date,reason):
        nonlocal pos,avg
        new=pos+frac; avg=(avg*pos+px*frac)/new if new>0 else 0.0; pos=new
        events.append((sym,date,'BUY',frac,px,reason))
    def sell(frac,px,date,reason):
        nonlocal pos,avg,net,cycle_net
        frac=min(frac,pos)
        if frac<=0:return
        pnl=((px/avg)-1)*100.0*frac-COST_RT*frac if avg>0 else 0.0
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
    inv=discover_symbols(); symbols=inv.symbol.astype(str).tolist()
    print('===== FUJIMOTO v0.3 FROZEN EXPANDED COVERAGE =====')
    print('RULES_UNCHANGED W1/W2/W3',W1,W2,W3,'COST_RT',COST_RT)
    print('MIN_DAYS',MIN_DAYS,'DISCOVERED_SYMBOLS',len(symbols))
    print('SYMBOLS',','.join(symbols))
    daily=load_daily(symbols); rows=[]; all_cycles=[]; all_events=[]
    for s,d in daily.items():
        z=indicators(d); net,ev,cy=run_symbol(s,z)
        rows.append((s,len(d),net,sum(e[5]=='STAGE1' for e in ev if e[2]=='BUY'),sum(e[5]=='STAGE2' for e in ev if e[2]=='BUY'),sum(e[5]=='STAGE3' for e in ev if e[2]=='BUY'),len(cy)))
        all_cycles.extend(cy); all_events.extend(ev)
    r=pd.DataFrame(rows,columns=['SYMBOL','DAYS','NET','S1','S2','S3','CYCLES'])
    active=r[r.CYCLES>0].sort_values('NET',ascending=False)
    print('\n===== ACTIVE SYMBOLS =====')
    print(active.round(3).to_string(index=False) if len(active) else 'NONE')
    print('\nTOTAL_SYMBOLS',len(r),'ACTIVE_SYMBOLS',len(active))
    print('TOTAL_DAYS',int(r.DAYS.sum()) if len(r) else 0)
    print('TOTAL_NET',f'{r.NET.sum():+.3f}%')
    print('TOTAL_CYCLES',len(all_cycles))
    print('STAGE1',int(r.S1.sum()),'STAGE2',int(r.S2.sum()),'STAGE3',int(r.S3.sum()))
    if all_cycles:
        a=np.asarray(all_cycles,float); pos=a[a>0].sum(); neg=-a[a<0].sum()
        print('WIN_RATE',f'{(a>0).mean()*100:.1f}%')
        print('PF','inf' if neg==0 else f'{pos/neg:.3f}')
        print('AVG_CYCLE',f'{a.mean():+.3f}%','WORST_CYCLE',f'{a.min():+.3f}%','BEST_CYCLE',f'{a.max():+.3f}%')
    print('\n===== EVENT COUNTS =====')
    if all_events: print(pd.Series([e[5] for e in all_events]).value_counts().to_string())
    else: print('NONE')
    print('\nNOTE: sample expansion only; no rule/threshold tuning performed.')

if __name__=='__main__': main()
