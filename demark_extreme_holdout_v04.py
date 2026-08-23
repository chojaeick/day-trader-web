#!/usr/bin/env python3
"""TOM DEMARK EXTREME EXHAUSTION v0.4 temporal holdout validation.

Freeze survivors from v0.3 discovery:
- DROP6 <= -1.50%
- HOLD = 6 or 12 x 5m bars
- TD9 definition unchanged: 9 consecutive closes below close 4 bars earlier
- No Heikin-Ashi entry filter
- Costs: 0.20 / 0.25 / 0.30% RT

Temporal split is deterministic and chosen without touching outcome values:
- TRAIN/DISCOVERY: earlier 70% of available trade dates
- HOLDOUT: latest 30% of available trade dates

The frozen rules are evaluated on HOLDOUT only for the decision.
DB read only. No auto order.
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
DROP_ABS=1.50
HOLDS=[6,12]
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
    out={}
    for (s,d),z in x.groupby(['symbol','trade_date'],sort=True):
        if len(z)<60: continue
        z=z.copy().reset_index(drop=True); z['bucket']=np.arange(len(z))//5
        b=(z.groupby('bucket',sort=True)
             .agg(time=('et_time','last'),open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
             .reset_index(drop=True))
        if len(b)>=20: out[(str(s),str(d))]=b
    return out


def td9_indices(b):
    c=b.close.to_numpy(float); n=len(c)
    cond=np.zeros(n,bool)
    if n>4: cond[4:]=c[4:]<c[:-4]
    td=np.zeros(n,int); k=0
    for i in range(n):
        if cond[i]: k+=1
        else: k=0
        td[i]=k
    return np.flatnonzero(td==TD_N)


def collect(data, allowed_dates, hold):
    rows=[]
    for (s,d),b in data.items():
        if d not in allowed_dates: continue
        c=b.close.to_numpy(float); h=b.high.to_numpy(float); l=b.low.to_numpy(float)
        for i in td9_indices(b):
            if i<6: continue
            drop6=(c[i]/c[i-6]-1.0)*100.0
            if drop6>-DROP_ABS: continue
            j=min(len(b)-1,i+hold)
            gross=(c[j]/c[i]-1.0)*100.0
            mfe=(np.max(h[i+1:j+1])/c[i]-1.0)*100.0 if j>i else 0.0
            mae=(np.min(l[i+1:j+1])/c[i]-1.0)*100.0 if j>i else 0.0
            rows.append((s,d,str(b.iloc[i].time),str(b.iloc[j].time),drop6,gross,mfe,mae))
    return pd.DataFrame(rows,columns=['symbol','date','entry_time','exit_time','drop6','gross','mfe','mae'])


def metrics(x,cost):
    if x.empty:
        return dict(TRADES=0,NET=0.,AVG=0.,WIN=0.,PF=0.,WORST=0.,POS_DATES=0,DATES=0)
    n=x.copy(); n['net']=n.gross-cost
    pos=n.loc[n.net>0,'net'].sum(); neg=-n.loc[n.net<0,'net'].sum(); bd=n.groupby('date').net.sum()
    return dict(TRADES=len(n),NET=float(n.net.sum()),AVG=float(n.net.mean()),WIN=float((n.net>0).mean()*100),
                PF=float(pos/neg) if neg>0 else float('inf'),WORST=float(n.net.min()),POS_DATES=int((bd>0).sum()),DATES=len(bd))


def main():
    syms=discover_symbols(); data=load_5m(syms)
    dates=sorted({d for _,d in data.keys()})
    cut=max(1,int(len(dates)*0.70))
    train=set(dates[:cut]); holdout=set(dates[cut:])
    print('===== TOM DEMARK EXTREME EXHAUSTION v0.4 TEMPORAL HOLDOUT =====')
    print('FROZEN DROP>=1.50% / HOLD 6,12 / TD9 UNCHANGED / DB READ ONLY')
    print('SYMBOLS',len(syms),'ALL_DATES',len(dates),'TRAIN_DATES',len(train),'HOLDOUT_DATES',len(holdout))
    print('TRAIN_RANGE',min(train),max(train))
    print('HOLDOUT_RANGE',min(holdout),max(holdout))
    survivors=[]
    for hold in HOLDS:
        x=collect(data,holdout,hold)
        print(f'\n===== HOLDOUT HOLD={hold} =====')
        print('TRADES_RAW',len(x),'MFE_AVG',round(float(x.mfe.mean()),3) if len(x) else 0.0,'MAE_AVG',round(float(x.mae.mean()),3) if len(x) else 0.0)
        all_ok=True
        for cost in COSTS:
            m=metrics(x,cost)
            print(f"COST {cost:.2f} TRADES {m['TRADES']} NET {m['NET']:+.3f}% AVG {m['AVG']:+.3f}% WIN {m['WIN']:.1f}% PF {m['PF']:.3f} WORST {m['WORST']:+.3f}% POS_DATES {m['POS_DATES']}/{m['DATES']}")
            all_ok &= (m['TRADES']>=20 and m['NET']>0 and m['PF']>1)
        if all_ok: survivors.append(hold)
        if len(x):
            q=x.copy(); q['net020']=q.gross-0.20
            by=q.groupby('symbol').agg(TRADES=('net020','size'),NET=('net020','sum'),AVG=('net020','mean'),WIN=('net020',lambda s:(s>0).mean()*100)).sort_values('NET',ascending=False)
            print('BY_SYMBOL_COST020')
            print(by.round(3).to_string())
    print('\n===== DECISION =====')
    print('HOLDOUT_SURVIVORS_ALL_COSTS',len(survivors),survivors)
    if survivors:
        print('DECISION RETEST / KEEP_COMPONENT: frozen rule survived this temporal holdout; next step is independent date-block or broader-universe validation, not tuning.')
    else:
        print('DECISION REJECT: discovery survivor failed temporal holdout. Do not retune DROP/HOLD using this holdout.')

if __name__=='__main__': main()
