#!/usr/bin/env python3
"""MA20_SCALP v0.6 - confirmation-entry probe.

Goal
- Keep the extreme-gap mean-reversion idea.
- Add structural confirmation before entry instead of finer threshold tuning.
- Search only a compact architecture grid.
- DB read-only. No auto orders.

Architecture
1) Gap trigger >= threshold.
2) Gap must start contracting.
3) Confirmation within N bars:
   - close > previous close
   - current low > minimum low of previous swing window (higher-low style defense)
4) Entry at confirmation close.
5) Exit at 25% recovery toward MA20-at-entry target.
6) Stop if either:
   - close breaks confirmation swing low, or
   - hard stop from entry is hit.

This is discovery on pre-20260722 training only. Any survivor must be frozen and sent to holdout.
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
GAPS=[2.5,2.75,3.0,3.25,3.5]
SWINGS=[3,5,8]
CONFIRM_BARS=[2,3,5]
HARD_STOPS=[0.50,0.75]
RECOVERY=0.25
MIN_GROSS=0.50
MAX_HOLD=30


def load_sessions():
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
        c=z.close.to_numpy(float); h=z.high.to_numpy(float); l=z.low.to_numpy(float)
        t=z.et_time.astype(str).to_numpy()
        ma=pd.Series(c).rolling(20,min_periods=20).mean().to_numpy(float)
        gap=(ma-c)/ma*100.0
        out.append((s,str(d),t,c,h,l,ma,gap))
    return out


def simulate_session(sess,gap_thr,swing,confirm_bars,hard_stop):
    s,d,t,c,h,l,ma,gap=sess
    trades=[]; i=max(21,swing+1); n=len(c)
    while i<n-2:
        if not np.isfinite(ma[i]): i+=1; continue
        g=float(gap[i]); pg=float(gap[i-1])
        if g>=gap_thr and g<pg:
            conf=None; swing_low=None
            endc=min(n-2,i+confirm_bars)
            for k in range(i,endc+1):
                if k-swing<0: continue
                prior_low=float(np.min(l[k-swing:k]))
                higher_low=float(l[k])>prior_low
                up_close=float(c[k])>float(c[k-1])
                if higher_low and up_close and np.isfinite(ma[k]):
                    conf=k; swing_low=prior_low; break
            if conf is None:
                i+=1; continue
            px=float(c[conf]); ma_e=float(ma[conf])
            target=px+(ma_e-px)*RECOVERY
            gross_target=(target/px-1.0)*100.0
            if gross_target<MIN_GROSS:
                i=conf+1; continue
            end=min(n-1,conf+MAX_HOLD); done=False
            for j in range(conf+1,end+1):
                hard_ret=(float(c[j])/px-1.0)*100.0
                structure_break=float(c[j])<swing_low
                hard_break=hard_ret<=-hard_stop
                # conservative: stop before target on same bar
                if structure_break or hard_break:
                    trades.append((s,d,t[conf],t[j],hard_ret,'STOP'))
                    i=j+1; done=True; break
                if float(h[j])>=target:
                    gross=(target/px-1.0)*100.0
                    trades.append((s,d,t[conf],t[j],gross,'TARGET'))
                    i=j+1; done=True; break
            if not done:
                gross=(float(c[end])/px-1.0)*100.0
                trades.append((s,d,t[conf],t[end],gross,'TIME'))
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
                WORST_DATE=float(bydate.min()),STOP_RATE=float(x.reason.eq('STOP').mean()*100.0))


def main():
    t0=time.time(); sessions=load_sessions()
    print('===== MA20 SCALP v0.6 CONFIRMATION ENTRY PROBE =====')
    print('DISCOVERY ONLY / DB READ ONLY / NO AUTO ORDER')
    print('TRAIN_MAX',TRAIN_MAX,'SESSIONS',len(sessions))
    print('RECOVERY',RECOVERY,'MIN_GROSS',MIN_GROSS,'MAX_HOLD',MAX_HOLD)
    rows=[]; total=len(GAPS)*len(SWINGS)*len(CONFIRM_BARS)*len(HARD_STOPS); k=0
    for g in GAPS:
        for sw in SWINGS:
            for cb in CONFIRM_BARS:
                for hs in HARD_STOPS:
                    k+=1; raw=[]
                    for sess in sessions:
                        raw.extend(simulate_session(sess,g,sw,cb,hs))
                    for cost in COSTS:
                        rows.append(dict(GAP=g,SWING=sw,CONFIRM_BARS=cb,HARD_STOP=hs,COST=cost,**summarize(raw,cost)))
                    if k%10==0 or k==total:
                        print(f'PROGRESS {k}/{total} elapsed={time.time()-t0:.1f}s')
    r=pd.DataFrame(rows)
    z=r[r.COST==0.20].copy()
    print('\n===== WIN>=90% WITH TRADES>=10 @ COST0.20 =====')
    q=z[(z.WIN_RATE>=90)&(z.TRADES>=10)].sort_values(['NET','TRADES'],ascending=[False,False])
    print('CASES',len(q))
    if len(q):
        print(q[['GAP','SWING','CONFIRM_BARS','HARD_STOP','TRADES','NET','AVG','WIN_RATE','PF','POS_DATES','DATES','WORST_DATE','STOP_RATE']].head(30).round(3).to_string(index=False))

    print('\n===== BEST NET WITH TRADES>=10 @ COST0.20 =====')
    q2=z[z.TRADES>=10].sort_values(['NET','WIN_RATE'],ascending=[False,False]).head(20)
    if len(q2):
        print(q2[['GAP','SWING','CONFIRM_BARS','HARD_STOP','TRADES','NET','AVG','WIN_RATE','PF','POS_DATES','DATES','WORST_DATE','STOP_RATE']].round(3).to_string(index=False))

    print('\n===== SURVIVE ALL COSTS =====')
    surv=[]
    for key,qq in r.groupby(['GAP','SWING','CONFIRM_BARS','HARD_STOP']):
        if len(qq)==3 and (qq.NET>0).all() and (qq.PF>1).all() and qq.TRADES.min()>=10 and qq.WIN_RATE.min()>=80:
            surv.append((*key,float(qq.NET.min()),int(qq.TRADES.min()),float(qq.WIN_RATE.min()),float(qq.PF.min())))
    print('SURVIVORS',len(surv))
    for x in sorted(surv,key=lambda v:(v[4],v[6]),reverse=True)[:20]:
        print('GAP',x[0],'SWING',x[1],'CONFIRM',x[2],'HARD_STOP',x[3],
              'MIN_NET',f'{x[4]:+.3f}%','TRADES',x[5],'MIN_WIN',f'{x[6]:.1f}%','MIN_PF',f'{x[7]:.3f}')
    print('RULE: freeze any chosen architecture and test unchanged on holdout. Do not micro-tune this window.')
    print('ELAPSED_SEC',f'{time.time()-t0:.2f}')

if __name__=='__main__': main()
