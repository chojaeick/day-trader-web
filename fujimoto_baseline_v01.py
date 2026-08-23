#!/usr/bin/env python3
"""FUJIMOTO BASELINE v0.1

Purpose
- First clean, reproducible Fujimoto-style baseline from existing minute DB.
- Daily bars are aggregated causally from REGULAR 1m data.
- Entry staging: 10% / 20% / 70%.
- Exit staging: 10% / 20% / 70%.
- Dynamic RSI uses rolling RSI distribution, with thresholds based only on prior data.
- No DB writes. No auto orders.

This is a baseline hypothesis test, not production logic.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']
COST_RT=0.20  # round-trip % assumption applied pro-rata to staged capital


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
    if x.empty:
        return {}
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
    delta=s.diff()
    up=delta.clip(lower=0)
    dn=-delta.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-(100/(1+rs))
    r= r.where(ad!=0,100.0)
    return r


def add_indicators(d):
    z=d.copy()
    c=z['close'].astype(float)
    h=z['high'].astype(float)
    l=z['low'].astype(float)
    z['rsi']=rsi14(c)

    # Dynamic RSI thresholds: prior 60 RSI observations only, then EMA(5).
    rprev=z['rsi'].shift(1)
    z['drsi_low_raw']=rprev.rolling(60,min_periods=30).quantile(0.20)
    z['drsi_mid_raw']=rprev.rolling(60,min_periods=30).quantile(0.50)
    z['drsi_high_raw']=rprev.rolling(60,min_periods=30).quantile(0.80)
    z['drsi_low']=z['drsi_low_raw'].ewm(span=5,adjust=False).mean()
    z['drsi_mid']=z['drsi_mid_raw'].ewm(span=5,adjust=False).mean()
    z['drsi_high']=z['drsi_high_raw'].ewm(span=5,adjust=False).mean()

    ema12=c.ewm(span=12,adjust=False).mean()
    ema26=c.ewm(span=26,adjust=False).mean()
    z['macd']=ema12-ema26
    z['macd_sig']=z['macd'].ewm(span=9,adjust=False).mean()
    z['macd_hist']=z['macd']-z['macd_sig']

    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2
    kijun=(h.rolling(26).max()+l.rolling(26).min())/2
    base_a=(tenkan+kijun)/2
    base_b=(h.rolling(52).max()+l.rolling(52).min())/2
    z['cloud_a']=base_a.shift(26)
    z['cloud_b']=base_b.shift(26)
    z['cloud_top']=z[['cloud_a','cloud_b']].max(axis=1)
    z['cloud_bot']=z[['cloud_a','cloud_b']].min(axis=1)
    z['chikou_bull']=c > c.shift(26)

    # Causal 5-bar confirmed pivot low: candidate at t-2 confirmed at t.
    pl=(l.shift(2)<l.shift(4))&(l.shift(2)<=l.shift(3))&(l.shift(2)<=l.shift(1))&(l.shift(2)<l)
    z['pivot_low_confirmed']=pl.fillna(False)
    z['pivot_price']=np.where(z['pivot_low_confirmed'],l.shift(2),np.nan)
    z['pivot_rsi']=np.where(z['pivot_low_confirmed'],z['rsi'].shift(2),np.nan)

    # Track last two confirmed pivots causally, then flag bullish divergence at confirmation bar.
    last_p=np.nan; last_r=np.nan; prev_p=np.nan; prev_r=np.nan
    bull=[]
    for i,row in z.iterrows():
        flag=False
        if bool(row['pivot_low_confirmed']) and np.isfinite(row['pivot_price']) and np.isfinite(row['pivot_rsi']):
            prev_p,prev_r=last_p,last_r
            last_p,last_r=float(row['pivot_price']),float(row['pivot_rsi'])
            if np.isfinite(prev_p) and np.isfinite(prev_r):
                flag=(last_p < prev_p) and (last_r > prev_r)
        bull.append(flag)
    z['bull_div']=bull

    z['rsi_cross_up_low']=(z['rsi'].shift(1)<=z['drsi_low'].shift(1))&(z['rsi']>z['drsi_low'])
    z['rsi_cross_down_mid']=(z['rsi'].shift(1)>=z['drsi_mid'].shift(1))&(z['rsi']<z['drsi_mid'])
    z['macd_dead']=(z['macd'].shift(1)>=z['macd_sig'].shift(1))&(z['macd']<z['macd_sig'])
    z['macd_bull']=z['macd']>z['macd_sig']
    z['cloud_break_up']=(c.shift(1)<=z['cloud_top'].shift(1))&(c>z['cloud_top'])
    z['cloud_break_down']=(c.shift(1)>=z['cloud_bot'].shift(1))&(c<z['cloud_bot'])
    return z


def backtest_symbol(sym,z):
    # position fractions of intended Fujimoto position: 0 / .1 / .3 / 1.0
    pos=0.0
    entry_cost_basis=0.0
    realized=0.0
    trades=[]
    div_latch=0
    stage1_date=None
    stage2_done=False

    def buy(frac,px,date,reason):
        nonlocal pos,entry_cost_basis
        if frac<=0: return
        new_pos=pos+frac
        entry_cost_basis=(entry_cost_basis*pos + px*frac)/new_pos if new_pos>0 else 0
        pos=new_pos
        trades.append((sym,date,'BUY',frac,px,reason))

    def sell(frac,px,date,reason):
        nonlocal pos,entry_cost_basis,realized
        frac=min(frac,pos)
        if frac<=0: return
        gross=((px/entry_cost_basis)-1)*100.0*frac if entry_cost_basis>0 else 0.0
        cost=COST_RT*frac
        realized += gross-cost
        pos-=frac
        trades.append((sym,date,'SELL',frac,px,reason,gross-cost))
        if pos<=1e-12:
            pos=0.0; entry_cost_basis=0.0

    for i in range(len(z)):
        row=z.iloc[i]
        px=float(row.close); date=str(row.trade_date)
        if bool(row.bull_div):
            div_latch=10  # valid for next 10 sessions
        elif div_latch>0:
            div_latch-=1

        # ENTRY 1: bullish divergence recently latched + Dynamic RSI lower cross
        if pos==0 and div_latch>0 and bool(row.rsi_cross_up_low):
            buy(0.10,px,date,'STAGE1_DIV+DRSI_CROSS')
            stage1_date=date; stage2_done=False; div_latch=0
            continue

        # ENTRY 2: second upward confirmation after stage1.
        if 0<pos<0.30 and not stage2_done:
            cond=(np.isfinite(row.drsi_mid) and row.rsi>row.drsi_mid and
                  row.macd_hist>0 and i>0 and row.macd_hist>z.iloc[i-1].macd_hist)
            if cond:
                buy(0.20,px,date,'STAGE2_RSI_MID+MACD_ACCEL')
                stage2_done=True
                continue

        # ENTRY 3: cloud breakout + bullish Chikou + MACD bullish
        if 0.29<=pos<1.0 and bool(row.cloud_break_up) and bool(row.chikou_bull) and bool(row.macd_bull):
            buy(0.70,px,date,'STAGE3_CLOUD+CHIKOU')
            continue

        # EXIT staging applies only to held fractions; one action per day, in sequence.
        if pos>0 and bool(row.macd_dead):
            sell(min(0.10,pos),px,date,'EXIT1_MACD_DEAD')
            continue
        if pos>0 and bool(row.rsi_cross_down_mid):
            sell(min(0.20,pos),px,date,'EXIT2_DRSI_MID_DOWN')
            continue
        if pos>0 and bool(row.cloud_break_down):
            sell(pos,px,date,'EXIT3_CLOUD_BREAKDOWN')
            stage2_done=False; stage1_date=None
            continue

    # mark-to-market any residual position at last close for comparable P&L
    if pos>0 and len(z):
        px=float(z.iloc[-1].close); date=str(z.iloc[-1].trade_date)
        sell(pos,px,date,'EOD_SAMPLE_CLOSE')
    return realized,trades


def main():
    daily=load_daily()
    print('===== FUJIMOTO BASELINE v0.1 =====')
    print('DAILY AGGREGATION FROM 1m REGULAR / DB READ ONLY / NO AUTO ORDER')
    print('COST_RT',COST_RT)
    all_tr=[]; rows=[]
    for s,d in daily.items():
        z=add_indicators(d)
        pnl,tr=backtest_symbol(s,z)
        rows.append((s,len(d),pnl,len([x for x in tr if x[2]=='BUY']),len([x for x in tr if x[2]=='SELL'])))
        all_tr.extend(tr)
    r=pd.DataFrame(rows,columns=['SYMBOL','DAYS','NET','BUY_EVENTS','SELL_EVENTS'])
    print('\n===== BY SYMBOL =====')
    print(r.round(3).to_string(index=False))
    print('\nTOTAL_NET',f'{r.NET.sum():+.3f}%')
    print('TOTAL_BUY_EVENTS',int(r.BUY_EVENTS.sum()))
    print('TOTAL_SELL_EVENTS',int(r.SELL_EVENTS.sum()))

    if all_tr:
        t=pd.DataFrame(all_tr)
        print('\n===== EVENT COUNTS =====')
        print(t[5].value_counts().to_string())
        print('\n===== FIRST 80 EVENTS =====')
        for x in all_tr[:80]: print(x)
    else:
        print('NO_EVENTS')

    print('\n===== BASELINE INTERPRETATION =====')
    print('This run is for signal/architecture sanity first. If Stage1/2/3 counts are too sparse, inspect indicator/event definitions before any parameter tuning.')

if __name__=='__main__':
    main()
