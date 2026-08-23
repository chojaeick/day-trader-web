#!/usr/bin/env python3
"""TOM DEMARK + HEIKIN-ASHI INTRADAY v0.2 SIGNAL QUALITY AUDIT

Purpose:
- Diagnose why v0.1 produced many TD9+HA entries but poor PnL.
- Do not tune thresholds and do not place orders.
- Compare raw TD9 forward behavior vs the first HA bullish confirmation within 6 bars.
- Measure confirmation delay, forward returns, MFE/MAE and simple context features.

5m bars from REGULAR 1m historical_minute_bars. DB read-only.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
MIN_DAYS=100
TD_N=9
ARM_BARS=6
HORIZONS=(1,3,6,12)


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
    out={}
    for (s,d),z in x.groupby(['symbol','trade_date'],sort=True):
        if len(z)<60: continue
        z=z.copy().reset_index(drop=True)
        z['bucket']=np.arange(len(z))//5
        b=(z.groupby('bucket',sort=True)
             .agg(time=('et_time','last'),open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
             .reset_index(drop=True))
        if len(b)>=20: out[(str(s),str(d))]=b
    return out


def add_ha_td(b):
    z=b.copy(); o=z.open.to_numpy(float); h=z.high.to_numpy(float); l=z.low.to_numpy(float); c=z.close.to_numpy(float)
    n=len(z)
    ha_c=(o+h+l+c)/4.0
    ha_o=np.empty(n,float); ha_o[0]=(o[0]+c[0])/2.0
    for i in range(1,n): ha_o[i]=(ha_o[i-1]+ha_c[i-1])/2.0
    bull=ha_c>ha_o; bear=ha_c<ha_o
    prev_bull=np.r_[False,bull[:-1]]; prev_bear=np.r_[False,bear[:-1]]
    z['ha_open']=ha_o; z['ha_close']=ha_c; z['ha_bull']=bull; z['ha_bear']=bear
    z['ha_bull_flip']=bull & (~prev_bull)
    z['ha_bear_flip']=bear & (~prev_bear)
    cond=np.zeros(n,bool)
    if n>4: cond[4:]=c[4:]<c[:-4]
    td=np.zeros(n,int); k=0
    for i in range(n):
        k=k+1 if cond[i] else 0; td[i]=k
    z['td_buy_count']=td; z['td9']=td==TD_N
    # context, all causal at event bar
    z['ret_3'] = z.close.pct_change(3)*100
    z['ret_6'] = z.close.pct_change(6)*100
    z['vol_ratio'] = z.volume / z.volume.rolling(12,min_periods=6).mean()
    z['ha_body_pct'] = np.abs(z.ha_close-z.ha_open)/z.close*100
    return z


def fwd_metrics(z,i):
    c=float(z.iloc[i].close); out={}
    for h in HORIZONS:
        j=i+h
        out[f'F{h}']=((float(z.iloc[j].close)/c-1)*100) if j<len(z) else np.nan
    j=min(len(z)-1,i+12)
    if j>i:
        out['MFE12']=(float(z.iloc[i+1:j+1].high.max())/c-1)*100
        out['MAE12']=(float(z.iloc[i+1:j+1].low.min())/c-1)*100
    else:
        out['MFE12']=np.nan; out['MAE12']=np.nan
    return out


def summarize(name,df):
    print(f'===== {name} =====')
    print('N',len(df))
    if df.empty: return
    for h in HORIZONS:
        s=df[f'F{h}'].dropna()
        if len(s): print(f'F{h}_AVG {s.mean():+.3f}%  POS {(s>0).mean()*100:.1f}%  MED {s.median():+.3f}%')
    print(f'MFE12_AVG {df.MFE12.mean():+.3f}%  MAE12_AVG {df.MAE12.mean():+.3f}%')


def main():
    syms=discover_symbols(); data=load_5m(syms)
    td_rows=[]; cf_rows=[]
    for (sym,day),b in data.items():
        z=add_ha_td(b)
        td_idx=np.flatnonzero(z.td9.to_numpy(bool))
        flips=np.flatnonzero(z.ha_bull_flip.to_numpy(bool))
        for i in td_idx:
            m=fwd_metrics(z,i)
            td_rows.append(dict(symbol=sym,date=day,time=str(z.iloc[i].time),idx=i,
                                ret3=float(z.iloc[i].ret_3) if pd.notna(z.iloc[i].ret_3) else np.nan,
                                ret6=float(z.iloc[i].ret_6) if pd.notna(z.iloc[i].ret_6) else np.nan,
                                vol_ratio=float(z.iloc[i].vol_ratio) if pd.notna(z.iloc[i].vol_ratio) else np.nan,**m))
            q=flips[(flips>=i)&(flips<=i+ARM_BARS)]
            if len(q):
                j=int(q[0]); mm=fwd_metrics(z,j)
                cf_rows.append(dict(symbol=sym,date=day,td_time=str(z.iloc[i].time),entry_time=str(z.iloc[j].time),
                                    delay=j-i,td_ret3=float(z.iloc[i].ret_3) if pd.notna(z.iloc[i].ret_3) else np.nan,
                                    td_ret6=float(z.iloc[i].ret_6) if pd.notna(z.iloc[i].ret_6) else np.nan,
                                    vol_ratio=float(z.iloc[j].vol_ratio) if pd.notna(z.iloc[j].vol_ratio) else np.nan,
                                    ha_body_pct=float(z.iloc[j].ha_body_pct),**mm))
    td=pd.DataFrame(td_rows); cf=pd.DataFrame(cf_rows)
    print('===== TOM DEMARK + HEIKIN-ASHI v0.2 SIGNAL QUALITY AUDIT =====')
    print('SYMBOLS',len(syms),'SYMBOL_DAYS',len(data),'TD9_EVENTS',len(td),'CONFIRMED',len(cf))
    print('NO PARAMETER TUNING / DB READ ONLY / NO AUTO ORDER')
    summarize('RAW TD9 FORWARD',td)
    print()
    summarize('FIRST HA BULL FLIP AFTER TD9',cf)
    if not cf.empty:
        print('\n===== CONFIRMATION DELAY =====')
        q=cf.groupby('delay').agg(N=('delay','size'),F3=('F3','mean'),F6=('F6','mean'),F12=('F12','mean'),POS6=('F6',lambda s:(s.dropna()>0).mean()*100),MFE12=('MFE12','mean'),MAE12=('MAE12','mean'))
        print(q.round(3).to_string())
        print('\n===== TD PRE-DROP QUARTILES AT CONFIRM =====')
        tmp=cf.dropna(subset=['td_ret6']).copy()
        if len(tmp)>=20:
            tmp['Q']=pd.qcut(tmp.td_ret6,4,duplicates='drop')
            q=tmp.groupby('Q',observed=True).agg(N=('F6','size'),TD_RET6=('td_ret6','mean'),F6=('F6','mean'),F12=('F12','mean'),POS6=('F6',lambda s:(s.dropna()>0).mean()*100),MFE12=('MFE12','mean'),MAE12=('MAE12','mean'))
            print(q.round(3).to_string())
        print('\n===== HA BODY QUARTILES =====')
        tmp=cf.dropna(subset=['ha_body_pct']).copy()
        if len(tmp)>=20:
            tmp['Q']=pd.qcut(tmp.ha_body_pct,4,duplicates='drop')
            q=tmp.groupby('Q',observed=True).agg(N=('F6','size'),HA_BODY=('ha_body_pct','mean'),F6=('F6','mean'),F12=('F12','mean'),POS6=('F6',lambda s:(s.dropna()>0).mean()*100))
            print(q.round(3).to_string())
    print('\n===== INTERPRETATION GUIDE =====')
    print('1) If RAW_TD9 forward returns are already negative, TD9 exhaustion itself is poor on 5m.')
    print('2) If RAW_TD9 is positive but HA-confirm is negative, HA confirmation timing/definition is the problem.')
    print('3) If only certain delay/context buckets are positive, treat them as hypotheses for a NEW training split; do not promote from this audit alone.')

if __name__=='__main__': main()
