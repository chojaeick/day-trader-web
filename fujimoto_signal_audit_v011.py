#!/usr/bin/env python3
"""FUJIMOTO v0.1.1 - signal sparsity audit.

Purpose
- Keep v0.1 indicator definitions unchanged.
- Diagnose exactly where Stage1/2/3 signals are being lost.
- No DB writes. No auto orders.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']


def load_daily():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR'
            AND symbol IN ({marks})
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
    s=pd.Series(c,dtype=float); d=s.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); ad=dn.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rs=au/ad.replace(0,np.nan); r=100-(100/(1+rs)); return r.where(ad!=0,100.0)


def add_indicators(d):
    z=d.copy(); c=z.close.astype(float); h=z.high.astype(float); l=z.low.astype(float)
    z['rsi']=rsi14(c)
    rprev=z.rsi.shift(1)
    z['drsi_low']=rprev.rolling(60,min_periods=30).quantile(.20).ewm(span=5,adjust=False).mean()
    z['drsi_mid']=rprev.rolling(60,min_periods=30).quantile(.50).ewm(span=5,adjust=False).mean()
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean()
    z['macd']=ema12-ema26; z['macd_sig']=z.macd.ewm(span=9,adjust=False).mean(); z['macd_hist']=z.macd-z.macd_sig
    tenkan=(h.rolling(9).max()+l.rolling(9).min())/2; kijun=(h.rolling(26).max()+l.rolling(26).min())/2
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
    z['rsi_cross_down_mid']=(z.rsi.shift(1)>=z.drsi_mid.shift(1))&(z.rsi<z.drsi_mid)
    z['macd_dead']=(z.macd.shift(1)>=z.macd_sig.shift(1))&(z.macd<z.macd_sig)
    z['macd_bull']=z.macd>z.macd_sig
    z['cloud_break_up']=(c.shift(1)<=z.cloud_top.shift(1))&(c>z.cloud_top)
    z['cloud_break_down']=(c.shift(1)>=z.cloud_bot.shift(1))&(c<z.cloud_bot)
    return z


def audit_symbol(sym,z):
    counts={k:0 for k in ['DAYS','RSI_VALID','DRSI_VALID','PIVOTS','BULL_DIV','DRSI_CROSS','DIV_LATCH_HIT','STAGE1','MID_RSI','MACD_ACCEL','STAGE2','CLOUD_UP','CHIKOU_BULL','MACD_BULL','STAGE3','MACD_DEAD','DRSI_MID_DOWN','CLOUD_DOWN']}
    counts['DAYS']=len(z); counts['RSI_VALID']=int(z.rsi.notna().sum()); counts['DRSI_VALID']=int(z.drsi_low.notna().sum())
    counts['PIVOTS']=int(z.pivot_low_confirmed.sum()); counts['BULL_DIV']=int(z.bull_div.sum()); counts['DRSI_CROSS']=int(z.rsi_cross_up_low.sum())
    counts['MID_RSI']=int((z.rsi>z.drsi_mid).fillna(False).sum()); counts['MACD_ACCEL']=int(((z.macd_hist>0)&(z.macd_hist>z.macd_hist.shift(1))).fillna(False).sum())
    counts['CLOUD_UP']=int(z.cloud_break_up.fillna(False).sum()); counts['CHIKOU_BULL']=int(z.chikou_bull.fillna(False).sum()); counts['MACD_BULL']=int(z.macd_bull.fillna(False).sum())
    counts['MACD_DEAD']=int(z.macd_dead.fillna(False).sum()); counts['DRSI_MID_DOWN']=int(z.rsi_cross_down_mid.fillna(False).sum()); counts['CLOUD_DOWN']=int(z.cloud_break_down.fillna(False).sum())
    div_latch=0; pos=0.; stage2=False
    for i,row in z.iterrows():
        if bool(row.bull_div): div_latch=10
        elif div_latch>0: div_latch-=1
        if div_latch>0 and bool(row.rsi_cross_up_low): counts['DIV_LATCH_HIT']+=1
        if pos==0 and div_latch>0 and bool(row.rsi_cross_up_low):
            counts['STAGE1']+=1; pos=.1; div_latch=0; stage2=False; continue
        if 0<pos<.3 and not stage2:
            cond=(np.isfinite(row.drsi_mid) and row.rsi>row.drsi_mid and row.macd_hist>0 and i>0 and row.macd_hist>z.iloc[i-1].macd_hist)
            if cond: counts['STAGE2']+=1; pos=.3; stage2=True; continue
        if .29<=pos<1 and bool(row.cloud_break_up) and bool(row.chikou_bull) and bool(row.macd_bull):
            counts['STAGE3']+=1; pos=1.; continue
        if pos>0 and bool(row.macd_dead): pos=max(0,pos-.1); continue
        if pos>0 and bool(row.rsi_cross_down_mid): pos=max(0,pos-.2); continue
        if pos>0 and bool(row.cloud_break_down): pos=0.; stage2=False; continue
    return counts


def main():
    daily=load_daily(); rows=[]
    for s,d in daily.items(): rows.append(dict(SYMBOL=s,**audit_symbol(s,add_indicators(d))))
    r=pd.DataFrame(rows)
    print('===== FUJIMOTO v0.1.1 SIGNAL SPARSITY AUDIT =====')
    print('DB READ ONLY / DEFINITIONS UNCHANGED FROM v0.1')
    print(r.to_string(index=False))
    print('\n===== TOTAL =====')
    t=r.drop(columns=['SYMBOL']).sum(numeric_only=True)
    for k,v in t.items(): print(k,int(v))
    print('\n===== BOTTLENECK RATIOS =====')
    def ratio(a,b): return 0.0 if b==0 else a/b*100
    print('BULL_DIV / PIVOTS',f"{ratio(t['BULL_DIV'],t['PIVOTS']):.1f}%")
    print('DRSI_CROSS / DRSI_VALID',f"{ratio(t['DRSI_CROSS'],t['DRSI_VALID']):.1f}%")
    print('DIV_LATCH_HIT / BULL_DIV',f"{ratio(t['DIV_LATCH_HIT'],t['BULL_DIV']):.1f}%")
    print('STAGE2 / STAGE1',f"{ratio(t['STAGE2'],t['STAGE1']):.1f}%")
    print('STAGE3 / STAGE2',f"{ratio(t['STAGE3'],t['STAGE2']):.1f}%")
    print('\nNEXT: loosen nothing yet. Identify the single largest bottleneck first, then change one architecture rule at a time.')

if __name__=='__main__': main()
