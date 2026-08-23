#!/usr/bin/env python3
"""MA20_SCALP v0.4 - entry-gap / stop-loss architecture probe.

Purpose:
- Keep the MA20 mean-reversion idea.
- Test whether larger entry gaps improve edge.
- Test explicit stop-loss structures.
- No symbol tuning. No auto orders. DB read-only.

Important:
This is a hypothesis probe, not a production strategy. It uses a broad pre-20260722
training window and reports a compact grid. Do not tune again on the same window after
selecting a candidate; any surviving candidate must be frozen and sent to a fresh holdout.
"""
from __future__ import annotations

import sqlite3, time
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DB=ROOT/'daytrader.db'
SYMS=['AMD','ARM','AVGO','INTC','NVDA','SMCI','TSM']
TRAIN_MAX='20260721'
COSTS=[0.20,0.25,0.30]
GAPS=[2.0,2.5,3.0,3.5,4.0]
RECOVERY=0.25
MIN_GROSS=0.50
MAX_HOLD=30

# Stop architectures:
# GAP_X: exit when MA20 gap expands entry_gap + X percentage points
# HARD_X: exit when price return from entry <= -X%
STOP_CASES=[('GAP_0.50','gap',0.50),('GAP_0.75','gap',0.75),('GAP_1.00','gap',1.00),
            ('HARD_0.40','hard',0.40),('HARD_0.60','hard',0.60),('HARD_0.80','hard',0.80)]

def load():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,et_time,open,high,low,close,volume
          FROM historical_minute_bars
          WHERE interval_min=1 AND session='REGULAR'
            AND trade_date<=? AND symbol IN ({marks})
          ORDER BY symbol,trade_date,et_time'''
    df=pd.read_sql_query(q,con,params=[TRAIN_MAX,*SYMS])
    con.close()
    out=[]
    for (s,d),z in df.groupby(['symbol','trade_date'],sort=True):
        if len(z)<40: continue
        c=z.close.to_numpy(float); h=z.high.to_numpy(float)
        # trailing 20-bar SMA, causal and including current close
        ma=pd.Series(c).rolling(20,min_periods=20).mean().to_numpy(float)
        gap=(ma-c)/ma*100.0
        out.append((s,str(d),z.et_time.astype(str).to_numpy(),c,h,ma,gap))
    return out

def simulate_session(sess,gap_thr,stop_kind,stop_val):
    s,d,t,c,h,ma,gap=sess
    trades=[]; i=21; n=len(c)
    while i<n-1:
        if not np.isfinite(ma[i]): i+=1; continue
        g=float(gap[i]); pg=float(gap[i-1]); px=float(c[i])
        target=px+(float(ma[i])-px)*RECOVERY
        tg=(target/px-1)*100.0
        if g>=gap_thr and g<pg and tg>=MIN_GROSS:
            eg=g; end=min(n-1,i+MAX_HOLD); done=False
            for j in range(i+1,end+1):
                # Conservative ordering: stop checked before target within same bar.
                ret=(float(c[j])/px-1)*100.0
                hit_stop=False
                if stop_kind=='gap':
                    hit_stop = np.isfinite(gap[j]) and float(gap[j]) >= eg+stop_val
                else:
                    hit_stop = ret <= -stop_val
                if hit_stop:
                    trades.append((s,d,t[i],t[j],ret,'STOP'))
                    i=j+1; done=True; break
                if float(h[j])>=target:
                    gross=(target/px-1)*100.0
                    trades.append((s,d,t[i],t[j],gross,'TARGET'))
                    i=j+1; done=True; break
            if not done:
                gross=(float(c[end])/px-1)*100.0
                trades.append((s,d,t[i],t[end],gross,'TIME'))
                i=end+1
        else:
            i+=1
    return trades

def summarize(raw,cost):
    if not raw:
        return dict(TRADES=0,NET=0.,AVG=0.,WIN_RATE=0.,PF=0.,POS_DATES=0,DATES=0,WORST_DATE=0.,STOP_RATE=0.)
    x=pd.DataFrame(raw,columns=['symbol','date','entry_time','exit_time','gross','reason'])
    x['net']=x.gross-cost
    pos=x.loc[x.net>0,'net'].sum(); neg=-x.loc[x.net<0,'net'].sum()
    bydate=x.groupby('date').net.sum()
    return dict(TRADES=len(x),NET=x.net.sum(),AVG=x.net.mean(),WIN_RATE=(x.net>0).mean()*100,
                PF=(pos/neg if neg>0 else float('inf')),POS_DATES=int((bydate>0).sum()),DATES=len(bydate),
                WORST_DATE=float(bydate.min()),STOP_RATE=(x.reason.eq('STOP').mean()*100))

def main():
    t0=time.time(); sessions=load()
    print('===== MA20 SCALP v0.4 ENTRY/STOP PROBE =====')
    print('TRAIN_MAX',TRAIN_MAX,'SESSIONS',len(sessions))
    print('RECOVERY',RECOVERY,'MIN_GROSS',MIN_GROSS,'MAX_HOLD',MAX_HOLD)
    rows=[]; total=len(GAPS)*len(STOP_CASES); k=0
    for gap_thr in GAPS:
        for stop_name,kind,val in STOP_CASES:
            k+=1
            raw=[]
            for sess in sessions:
                raw.extend(simulate_session(sess,gap_thr,kind,val))
            for cost in COSTS:
                m=summarize(raw,cost)
                rows.append(dict(GAP=gap_thr,STOP=stop_name,COST=cost,**m))
            if k%5==0 or k==total:
                print(f'PROGRESS {k}/{total} elapsed={time.time()-t0:.1f}s')
    r=pd.DataFrame(rows)
    print('\n===== COST 0.20 SUMMARY =====')
    z=r[r.COST==0.20].sort_values(['NET','PF'],ascending=False)
    print(z[['GAP','STOP','TRADES','NET','AVG','WIN_RATE','PF','POS_DATES','DATES','WORST_DATE','STOP_RATE']].round(3).to_string(index=False))
    print('\n===== SURVIVE ALL COSTS =====')
    surv=[]
    for (g,st),q in r.groupby(['GAP','STOP']):
        if len(q)==3 and (q.NET>0).all() and (q.PF>1).all() and q.TRADES.min()>=20:
            surv.append((g,st,float(q.NET.min()),int(q.TRADES.min()),float(q.PF.min())))
    print('SURVIVORS',len(surv))
    for x in sorted(surv,key=lambda v:v[2],reverse=True):
        print('GAP',x[0],'STOP',x[1],'MIN_NET',f'{x[2]:+.3f}%','TRADES',x[3],'MIN_PF',f'{x[4]:.3f}')
    print('RULE: if any survivor is selected, freeze it and test on fresh holdout; do not retune this training window.')
    print('ELAPSED_SEC',f'{time.time()-t0:.2f}')

if __name__=='__main__': main()
