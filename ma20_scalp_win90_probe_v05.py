#!/usr/bin/env python3
"""MA20_SCALP v0.5 - exploratory search for >=90% win-rate region.

Goal
- Explore whether the extreme-gap MA20 mean-reversion idea has a contiguous high-win-rate zone.
- Search GAP threshold and explicit stop architecture only.
- Keep RECOVERY=25%, MIN_GROSS=0.5%, MAX_HOLD=30 fixed.
- Use the same pre-20260722 research window only for discovery.

Important
- This is TRAINING / DISCOVERY, not validation.
- Any candidate found here MUST be frozen and tested on a separate holdout/future window.
- No symbol tuning, DB write, or auto order.
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
GAPS=[2.50,2.75,3.00,3.25,3.50,3.75,4.00]
RECOVERY=0.25
MIN_GROSS=0.50
MAX_HOLD=30
STOP_CASES=[
    ('GAP_0.50','gap',0.50),('GAP_0.60','gap',0.60),('GAP_0.75','gap',0.75),
    ('GAP_0.90','gap',0.90),('GAP_1.00','gap',1.00),
    ('HARD_0.30','hard',0.30),('HARD_0.40','hard',0.40),('HARD_0.50','hard',0.50),
    ('HARD_0.60','hard',0.60),('HARD_0.80','hard',0.80),
]


def load():
    con=sqlite3.connect(DB)
    marks=','.join('?' for _ in SYMS)
    q=f'''SELECT symbol,trade_date,et_time,high,close
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
        ma=pd.Series(c).rolling(20,min_periods=20).mean().to_numpy(float)
        gap=(ma-c)/ma*100.0
        out.append((s,str(d),z.et_time.astype(str).to_numpy(),c,h,ma,gap))
    return out


def simulate_session(sess,gap_thr,stop_kind,stop_val):
    s,d,t,c,h,ma,gap=sess
    trades=[]; i=21; n=len(c)
    while i<n-1:
        g=gap[i]
        if not np.isfinite(g): i+=1; continue
        px=c[i]
        target=px+(ma[i]-px)*RECOVERY
        target_gross=(target/px-1.0)*100.0
        if g>=gap_thr and g<gap[i-1] and target_gross>=MIN_GROSS:
            entry_gap=float(g); end=min(n-1,i+MAX_HOLD); done=False
            for j in range(i+1,end+1):
                ret=(c[j]/px-1.0)*100.0
                if stop_kind=='gap':
                    hit_stop=np.isfinite(gap[j]) and gap[j]>=entry_gap+stop_val
                else:
                    hit_stop=ret<=-stop_val
                # conservative ordering: stop first within same 1m bar
                if hit_stop:
                    trades.append((s,d,t[i],t[j],float(ret),'STOP'))
                    i=j+1; done=True; break
                if h[j]>=target:
                    gross=(target/px-1.0)*100.0
                    trades.append((s,d,t[i],t[j],float(gross),'TARGET'))
                    i=j+1; done=True; break
            if not done:
                gross=(c[end]/px-1.0)*100.0
                trades.append((s,d,t[i],t[end],float(gross),'TIME'))
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
    return dict(TRADES=len(x),NET=x.net.sum(),AVG=x.net.mean(),WIN_RATE=(x.net>0).mean()*100.0,
                PF=(pos/neg if neg>0 else float('inf')),POS_DATES=int((bydate>0).sum()),DATES=len(bydate),
                WORST_DATE=float(bydate.min()),STOP_RATE=x.reason.eq('STOP').mean()*100.0)


def main():
    t0=time.time(); sessions=load()
    print('===== MA20 SCALP v0.5 WIN>=90% REGION PROBE =====')
    print('DISCOVERY ONLY / DB READ ONLY / NO AUTO ORDER')
    print('TRAIN_MAX',TRAIN_MAX,'SESSIONS',len(sessions))
    print('RECOVERY',RECOVERY,'MIN_GROSS',MIN_GROSS,'MAX_HOLD',MAX_HOLD)
    rows=[]; total=len(GAPS)*len(STOP_CASES); k=0
    for g in GAPS:
        for stop_name,kind,val in STOP_CASES:
            k+=1
            raw=[]
            for sess in sessions:
                raw.extend(simulate_session(sess,g,kind,val))
            for cost in COSTS:
                rows.append(dict(GAP=g,STOP=stop_name,COST=cost,**summarize(raw,cost)))
            if k%10==0 or k==total:
                print(f'PROGRESS {k}/{total} elapsed={time.time()-t0:.1f}s')

    r=pd.DataFrame(rows)
    r20=r[r.COST==0.20].copy()

    print('\n===== >=90% WIN RATE @ COST 0.20 =====')
    w=r20[(r20.WIN_RATE>=90.0)&(r20.TRADES>0)].sort_values(['TRADES','NET'],ascending=[False,False])
    print('WIN90_CASES',len(w))
    if len(w):
        print(w[['GAP','STOP','TRADES','NET','AVG','WIN_RATE','PF','POS_DATES','DATES','WORST_DATE','STOP_RATE']].round(3).to_string(index=False))

    print('\n===== >=80% WIN RATE WITH TRADES>=5 @ COST 0.20 =====')
    w80=r20[(r20.WIN_RATE>=80.0)&(r20.TRADES>=5)].sort_values(['WIN_RATE','TRADES','NET'],ascending=[False,False,False])
    if len(w80):
        print(w80[['GAP','STOP','TRADES','NET','AVG','WIN_RATE','PF','POS_DATES','DATES','WORST_DATE','STOP_RATE']].round(3).to_string(index=False))
    else:
        print('NONE')

    print('\n===== COST ROBUSTNESS OF WIN90 DISCOVERY CASES =====')
    keys={(float(x.GAP),str(x.STOP)) for _,x in w.iterrows()}
    for g,st in sorted(keys):
        q=r[(r.GAP==g)&(r.STOP==st)].sort_values('COST')
        print(f'-- GAP {g:.2f} STOP {st} --')
        print(q[['COST','TRADES','NET','AVG','WIN_RATE','PF','WORST_DATE']].round(3).to_string(index=False))

    print('\n===== CONTIGUOUS GAP SUMMARY (BEST STOP PER GAP @ COST0.20) =====')
    for g,q in r20.groupby('GAP'):
        q=q.sort_values(['WIN_RATE','TRADES','NET'],ascending=[False,False,False])
        x=q.iloc[0]
        print(f'GAP {g:.2f} BEST_STOP {x.STOP} TRADES {int(x.TRADES)} WIN {x.WIN_RATE:.1f}% NET {x.NET:+.3f}% PF {x.PF:.3f}')

    print('\n===== DECISION SUPPORT =====')
    qualified=w[(w.TRADES>=5)&(w.NET>0)&(w.PF>1.0)]
    print('WIN90_WITH_5PLUS_TRADES',len(qualified))
    if len(qualified):
        best=qualified.sort_values(['TRADES','NET'],ascending=[False,False]).iloc[0]
        print('DISCOVERY_CANDIDATE',f"GAP={best.GAP:.2f}",f"STOP={best.STOP}",f"TRADES={int(best.TRADES)}",f"WIN={best.WIN_RATE:.1f}%",f"NET={best.NET:+.3f}%")
        print('NEXT: freeze this exact candidate and test on unseen holdout. Do not retune using holdout.')
    else:
        print('DISCOVERY_CANDIDATE NONE')
        print('NEXT: do not force a 90% threshold by finer tuning; change entry confirmation architecture instead.')
    print('ELAPSED_SEC',f'{time.time()-t0:.2f}')

if __name__=='__main__': main()
