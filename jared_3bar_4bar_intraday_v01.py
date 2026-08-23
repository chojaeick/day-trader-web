#!/usr/bin/env python3
"""JARED 3-BAR / 4-BAR PLAY INTRADAY v0.1

Architecture screen only. Causal, DB read-only, no auto order.
- Source: REGULAR 1m historical_minute_bars.
- Test both native 1m and causal 5m aggregation.
- Long-only baseline first.
- Pattern hypothesis based on the public 3-Bar/4-Bar Play concept:
  1) Igniting bar: strong bullish body, close near bar high, range expansion and volume expansion.
  2) 3-Bar: one narrow inside/consolidation bar.
     4-Bar: two narrow consolidation bars contained within igniting-bar range.
  3) Entry on next-bar breakout above consolidation high.
  4) Stop at consolidation low.
  5) Exit at fixed R target (1R / 2R) or EOD.
- Costs: 0.20 / 0.25 / 0.30% round trip.

This is a transparent baseline approximation, not a claim that it reproduces Jared Wesley's
proprietary/custom indicator shown in videos. If the public-pattern baseline shows edge, freeze
it and move to temporal OOS before any retuning.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'daytrader.db'
MIN_DAYS = 100
COSTS = [0.20, 0.25, 0.30]
R_TARGETS = [1.0, 2.0]


def discover_symbols():
    con = sqlite3.connect(DB)
    q = """
    SELECT symbol, COUNT(DISTINCT trade_date) AS days
    FROM historical_minute_bars
    WHERE interval_min=1 AND session='REGULAR'
    GROUP BY symbol
    HAVING days >= ?
    ORDER BY symbol
    """
    z = pd.read_sql_query(q, con, params=[MIN_DAYS])
    con.close()
    return z.symbol.astype(str).tolist()


def load_1m(symbols):
    if not symbols:
        return {}
    con = sqlite3.connect(DB)
    marks = ','.join('?' for _ in symbols)
    q = f"""
    SELECT symbol,trade_date,et_time,open,high,low,close,volume
    FROM historical_minute_bars
    WHERE interval_min=1 AND session='REGULAR' AND symbol IN ({marks})
    ORDER BY symbol,trade_date,et_time
    """
    x = pd.read_sql_query(q, con, params=symbols)
    con.close()
    out = {}
    for (s,d), z in x.groupby(['symbol','trade_date'], sort=True):
        if len(z) >= 60:
            out[(str(s),str(d))] = z.reset_index(drop=True)
    return out


def to_5m(z):
    q = z.copy().reset_index(drop=True)
    q['bucket'] = np.arange(len(q)) // 5
    b = (q.groupby('bucket', sort=True)
           .agg(time=('et_time','last'), open=('open','first'), high=('high','max'),
                low=('low','min'), close=('close','last'), volume=('volume','sum'))
           .reset_index(drop=True))
    return b


def prep(z):
    q = z.copy()
    if 'time' not in q.columns:
        q = q.rename(columns={'et_time':'time'})
    o=q.open.astype(float); h=q.high.astype(float); l=q.low.astype(float); c=q.close.astype(float); v=q.volume.astype(float)
    rng=(h-l).replace(0,np.nan)
    body=(c-o).abs()
    q['bull']=c>o
    q['body_frac']=(body/rng).fillna(0)
    q['close_loc']=((c-l)/rng).fillna(0)
    q['range_med20']=rng.shift(1).rolling(20,min_periods=10).median()
    q['vol_med20']=v.shift(1).rolling(20,min_periods=10).median()
    q['range_exp']=rng/q.range_med20
    q['vol_exp']=v/q.vol_med20.replace(0,np.nan)
    return q


def detect_and_simulate(sym, day, z, pattern_len, r_target):
    q=prep(z)
    trades=[]; signals=0
    n=len(q)
    # pattern_len=3 => igniting + 1 consolidation + breakout
    # pattern_len=4 => igniting + 2 consolidation + breakout
    cons_n = pattern_len - 2
    i=20
    while i < n-1:
        a=q.iloc[i]
        ignite = bool(a.bull) and a.body_frac>=0.65 and a.close_loc>=0.80 and a.range_exp>=1.50 and a.vol_exp>=1.20
        if not ignite:
            i+=1; continue
        if i+cons_n >= n-1:
            break
        cons=q.iloc[i+1:i+1+cons_n]
        a_hi=float(a.high); a_lo=float(a.low); a_rng=a_hi-a_lo
        if a_rng<=0:
            i+=1; continue
        inside=((cons.high.astype(float)<=a_hi) & (cons.low.astype(float)>=a_lo)).all()
        narrow=((cons.high.astype(float)-cons.low.astype(float)) <= 0.65*a_rng).all()
        if not (inside and narrow):
            i+=1; continue
        signals+=1
        breakout_i=i+1+cons_n
        br=q.iloc[breakout_i]
        cons_hi=float(cons.high.astype(float).max()); cons_lo=float(cons.low.astype(float).min())
        if float(br.high) <= cons_hi:
            i+=1; continue
        entry=max(cons_hi, float(br.open))
        risk=entry-cons_lo
        if risk<=0:
            i+=1; continue
        target=entry+r_target*risk
        exit_px=float(br.close); exit_i=breakout_i; reason='EOD'
        # assume if same bar touches both stop and target, stop first = conservative
        for j in range(breakout_i, n):
            row=q.iloc[j]
            if float(row.low) <= cons_lo:
                exit_px=cons_lo; exit_i=j; reason='STOP'; break
            if float(row.high) >= target:
                exit_px=target; exit_i=j; reason=f'TP_{r_target:.0f}R'; break
            if j==n-1:
                exit_px=float(row.close); exit_i=j; reason='EOD'; break
        gross=(exit_px/entry-1.0)*100.0
        trades.append((sym,day,str(q.iloc[breakout_i].time),str(q.iloc[exit_i].time),pattern_len,r_target,entry,cons_lo,target,exit_px,gross,reason))
        i=exit_i+1
    return signals,trades


def summarize(trades,cost):
    if not trades:
        return dict(TRADES=0,NET=0.,AVG=0.,WIN_RATE=0.,PF=0.,WORST=0.,POS_DATES=0,DATES=0,STOP_RATE=0.)
    x=pd.DataFrame(trades,columns=['symbol','date','entry_time','exit_time','pattern','r_target','entry','stop','target','exit','gross','reason'])
    x['net']=x.gross-cost
    pos=x.loc[x.net>0,'net'].sum(); neg=-x.loc[x.net<0,'net'].sum(); bydate=x.groupby('date').net.sum()
    return dict(TRADES=len(x),NET=float(x.net.sum()),AVG=float(x.net.mean()),WIN_RATE=float((x.net>0).mean()*100),
                PF=(float(pos/neg) if neg>0 else float('inf')),WORST=float(x.net.min()),
                POS_DATES=int((bydate>0).sum()),DATES=len(bydate),STOP_RATE=float((x.reason=='STOP').mean()*100))


def run_tf(label,data_map,make5=False):
    print(f'\n===== {label} =====')
    rows=[]
    for pattern in (3,4):
        for rt in R_TARGETS:
            signals=0; trades=[]
            for (s,d), raw in data_map.items():
                z=to_5m(raw) if make5 else raw
                if len(z)<30: continue
                sig,tr=detect_and_simulate(s,d,z,pattern,rt)
                signals+=sig; trades.extend(tr)
            for cost in COSTS:
                m=summarize(trades,cost)
                rows.append((pattern,rt,signals,cost,*m.values()))
    cols=['PATTERN','R_TARGET','SIGNALS','COST','TRADES','NET','AVG','WIN_RATE','PF','WORST','POS_DATES','DATES','STOP_RATE']
    r=pd.DataFrame(rows,columns=cols)
    print(r.round(3).to_string(index=False))
    survivors=[]
    for (p,rt),g in r.groupby(['PATTERN','R_TARGET']):
        if (g.TRADES.min()>=30 and (g.NET>0).all() and (g.PF>1).all()):
            survivors.append((int(p),float(rt),int(g.TRADES.min()),float(g.NET.min()),float(g.PF.min())))
    print('SURVIVORS_ALL_COSTS',len(survivors),survivors)


def main():
    syms=discover_symbols(); data=load_1m(syms)
    print('===== JARED 3-BAR / 4-BAR PLAY INTRADAY v0.1 =====')
    print('PUBLIC-PATTERN APPROXIMATION / CAUSAL / LONG ONLY / DB READ ONLY / NO AUTO ORDER')
    print('SYMBOLS',len(syms),','.join(syms),'SYMBOL_DAYS',len(data))
    print('IGNITE body>=65% close_loc>=80% range_exp>=1.5x vol_exp>=1.2x')
    print('CONSOLIDATION inside igniting range and range<=65% of igniting range')
    run_tf('1-MINUTE',data,make5=False)
    run_tf('5-MINUTE',data,make5=True)
    print('\nDECISION RULE: discovery only. Do not tune this sample. Any survivor -> freeze -> temporal OOS.')

if __name__=='__main__':
    main()
