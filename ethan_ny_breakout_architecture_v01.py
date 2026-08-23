#!/usr/bin/env python3
"""ETHAN GARLAND NY BREAKOUT - SOURCE-CONSTRAINED ARCHITECTURE SCREEN v0.1

Purpose
-------
Test the *architecture* supported by the user's captured Ethan Garland course/video material,
without pretending that subjective zone rules have already been reverse engineered exactly.

Source-supported rules used here
--------------------------------
- Asset focus: NQ/MNQ or NAS100; QQQ is used here only as a DB-available Nasdaq proxy.
- Time focus: New York session, from 09:30 ET.
- 5-minute chart.
- Identify a prior range / support-resistance zone.
- Require a breakout AND close outside the zone.
- Do NOT chase the breakout: wait for a retest of the broken zone.
- If return to the entry zone is slow/corrective (multiple small candles), entry may be immediate.
- If return is momentum/fast, do not catch a falling knife; wait for rejection.
- 50 SMA is observed as a confluence.
- Trade management benchmark: 1:1, 1:1.5, 1:2 R/R; source material also references BE management.

Important approximation in this v0.1
------------------------------------
The original course defines 4H/5m zones visually via significant V-shaped reactions,
multiple interactions/wicks, buddy candles, etc. Those exact rules are not fully numeric yet.
For this architecture screen ONLY, a causal rolling 5m support/resistance proxy is used.
This script therefore MUST NOT be called an exact reproduction or used for live auto-ordering.

DB READ ONLY / NO AUTO ORDER / CAUSAL
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'daytrader.db'
SYMBOL = 'QQQ'  # closest available proxy to NQ/NAS100 in current historical DB
COSTS = [0.20, 0.25, 0.30]
R_TARGETS = [1.0, 1.5, 2.0]

# Architecture-screen proxy parameters. Keep coarse; do not micro-tune on this sample.
ZONE_LOOKBACK = 18       # prior 90 minutes of 5m bars
ZONE_PAD_FRAC = 0.12     # zone thickness as fraction of prior median 5m range
BREAK_MIN_FRAC = 0.05    # close must clear zone boundary by >=5% of median 5m range
RETEST_MAX_BARS = 8      # up to 40 min for retest
SLOW_MIN_BARS = 2        # source says multiple small candles
SLOW_MAX_BODY_RATIO = 0.70
MOMENTUM_BODY_RATIO = 1.25
REJECTION_WICK_MIN = 0.35
MAX_HOLD_BARS = 24       # 2h after entry


def load_1m(symbol: str):
    con = sqlite3.connect(DB)
    q = """
    SELECT trade_date,et_time,open,high,low,close,volume
    FROM historical_minute_bars
    WHERE interval_min=1 AND session='REGULAR' AND symbol=?
    ORDER BY trade_date,et_time
    """
    x = pd.read_sql_query(q, con, params=[symbol])
    con.close()
    out = {}
    for d,z in x.groupby('trade_date', sort=True):
        z=z.reset_index(drop=True)
        if len(z)>=300:
            out[str(d)] = z
    return out


def to_5m(z):
    q=z.copy().reset_index(drop=True)
    q['bucket']=np.arange(len(q))//5
    b=(q.groupby('bucket',sort=True)
        .agg(time=('et_time','last'),open=('open','first'),high=('high','max'),
             low=('low','min'),close=('close','last'),volume=('volume','sum'))
        .reset_index(drop=True))
    for c in ['open','high','low','close','volume']:
        b[c]=pd.to_numeric(b[c],errors='coerce')
    b=b.dropna(subset=['open','high','low','close']).reset_index(drop=True)
    b['rng']=b.high-b.low
    b['body']=(b.close-b.open).abs()
    b['sma50']=b.close.rolling(50,min_periods=50).mean()
    b['med_rng12']=b.rng.shift(1).rolling(12,min_periods=6).median()
    return b


def in_ny_window(t: str):
    # 5m buckets are labeled by last 1m timestamp. Keep regular session architecture only.
    try:
        hh,mm=[int(x) for x in str(t)[:5].split(':')]
        mins=hh*60+mm
        return 9*60+34 <= mins <= 15*60+30
    except Exception:
        return True


def wick_rejection(row, side):
    rng=max(float(row.high-row.low),1e-12)
    if side=='LONG':
        lower=min(float(row.open),float(row.close))-float(row.low)
        return lower/rng >= REJECTION_WICK_MIN and float(row.close)>=float(row.open)
    upper=float(row.high)-max(float(row.open),float(row.close))
    return upper/rng >= REJECTION_WICK_MIN and float(row.close)<=float(row.open)


def simulate_day(day: str, raw):
    z=to_5m(raw)
    trades=[]; candidates=0; slow_n=0; momentum_n=0; rejection_n=0
    if len(z)<60: return candidates,slow_n,momentum_n,rejection_n,trades

    i=max(50,ZONE_LOOKBACK)
    while i < len(z)-2:
        if not in_ny_window(z.iloc[i].time):
            i+=1; continue
        prev=z.iloc[i-ZONE_LOOKBACK:i]
        med=float(prev.rng.median()) if len(prev) else 0.0
        if not np.isfinite(med) or med<=0:
            i+=1; continue

        # Causal proxy only: prior rolling extremes become zone center; pad creates a wick-inclusive band.
        res=float(prev.high.max()); sup=float(prev.low.min()); pad=ZONE_PAD_FRAC*med
        res_lo,res_hi=res-pad,res+pad
        sup_lo,sup_hi=sup-pad,sup+pad
        row=z.iloc[i]
        long_break = float(row.close) > res_hi + BREAK_MIN_FRAC*med
        short_break = float(row.close) < sup_lo - BREAK_MIN_FRAC*med
        if not (long_break or short_break):
            i+=1; continue
        candidates+=1
        side='LONG' if long_break else 'SHORT'
        zone_lo,zone_hi=(res_lo,res_hi) if side=='LONG' else (sup_lo,sup_hi)
        breakout_body=max(float(row.body),1e-12)

        # Wait for a causal retest of the broken zone.
        touched=[]; entry_i=None; retest_type=None
        for j in range(i+1,min(len(z),i+1+RETEST_MAX_BARS)):
            r=z.iloc[j]
            touch=float(r.low)<=zone_hi and float(r.high)>=zone_lo
            if not touch:
                # collect approach candles only after price is on breakout side
                if side=='LONG' and float(r.close)>zone_hi: touched.append(j)
                elif side=='SHORT' and float(r.close)<zone_lo: touched.append(j)
                continue

            approach=touched[-3:]
            approach_bodies=[float(z.iloc[k].body) for k in approach]
            avg_body=np.mean(approach_bodies) if approach_bodies else float(r.body)
            # 'slow/corrective' = multiple smaller approach bars than breakout impulse.
            is_slow=(len(approach)>=SLOW_MIN_BARS and avg_body <= SLOW_MAX_BODY_RATIO*breakout_body)
            is_momentum=(float(r.body) >= MOMENTUM_BODY_RATIO*breakout_body or (len(approach)<=1 and float(r.body)>breakout_body))

            if is_slow:
                slow_n+=1; retest_type='SLOW_DIRECT'; entry_i=j
                break
            if is_momentum:
                momentum_n+=1
                # Source says wait for rejection; allow current or next two bars to reject the zone.
                for k in range(j,min(len(z),j+3)):
                    rr=z.iloc[k]
                    if wick_rejection(rr,side):
                        rejection_n+=1; retest_type='MOMENTUM_REJECTION'; entry_i=k; break
                if entry_i is not None: break
                # no rejection => no trade
                break

            # Neutral retest: require rejection rather than guessing.
            if wick_rejection(r,side):
                rejection_n+=1; retest_type='NEUTRAL_REJECTION'; entry_i=j; break
            break

        if entry_i is None:
            i+=1; continue

        e=z.iloc[entry_i]
        # Enter at retest/rejection close. Stop beyond zone plus one zone pad.
        entry=float(e.close)
        if side=='LONG':
            stop=min(float(e.low),zone_lo-pad)
            risk=entry-stop
            sma_ok=bool(np.isfinite(e.sma50) and entry>=float(e.sma50))
        else:
            stop=max(float(e.high),zone_hi+pad)
            risk=stop-entry
            sma_ok=bool(np.isfinite(e.sma50) and entry<=float(e.sma50))
        if risk<=0:
            i+=1; continue

        # We record each R target from the exact same frozen entry architecture.
        for rt in R_TARGETS:
            target=entry + rt*risk if side=='LONG' else entry-rt*risk
            exit_px=float(e.close); reason='TIME'; exit_i=entry_i
            for k in range(entry_i+1,min(len(z),entry_i+1+MAX_HOLD_BARS)):
                rr=z.iloc[k]
                # conservative same-bar ordering: stop first
                if side=='LONG':
                    if float(rr.low)<=stop:
                        exit_px=stop; reason='STOP'; exit_i=k; break
                    if float(rr.high)>=target:
                        exit_px=target; reason=f'TP_{rt}R'; exit_i=k; break
                else:
                    if float(rr.high)>=stop:
                        exit_px=stop; reason='STOP'; exit_i=k; break
                    if float(rr.low)<=target:
                        exit_px=target; reason=f'TP_{rt}R'; exit_i=k; break
                exit_px=float(rr.close); exit_i=k
            gross=((exit_px/entry-1)*100) if side=='LONG' else ((entry/exit_px-1)*100)
            trades.append((day,side,str(z.iloc[i].time),str(e.time),retest_type,sma_ok,rt,entry,stop,target,exit_px,gross,reason))
        i=max(i+1,entry_i+1)
    return candidates,slow_n,momentum_n,rejection_n,trades


def metrics(x,cost):
    q=x.copy(); q['net']=q.gross-cost
    pos=q.loc[q.net>0,'net'].sum(); neg=-q.loc[q.net<0,'net'].sum()
    bydate=q.groupby('date').net.sum()
    return {
        'TRADES':len(q),'NET':q.net.sum(),'AVG':q.net.mean(),'WIN_RATE':(q.net>0).mean()*100,
        'PF':(pos/neg if neg>0 else float('inf')),'WORST':q.net.min(),
        'POS_DATES':int((bydate>0).sum()),'DATES':len(bydate),'STOP_RATE':(q.reason=='STOP').mean()*100,
        'SMA_OK_RATE':q.sma_ok.mean()*100,
    }


def main():
    data=load_1m(SYMBOL)
    all_tr=[]; cand=slow=mom=rej=0
    for day,raw in data.items():
        a,b,c,d,tr=simulate_day(day,raw)
        cand+=a; slow+=b; mom+=c; rej+=d; all_tr.extend(tr)
    print('===== ETHAN NY BREAKOUT ARCHITECTURE v0.1 =====')
    print('SOURCE-CONSTRAINED / QQQ PROXY FOR NQ / 5M / CAUSAL / DB READ ONLY / NO AUTO ORDER')
    print('DAYS',len(data),'BREAKOUT_CANDIDATES',cand,'SLOW_RETURNS',slow,'MOMENTUM_RETURNS',mom,'REJECTIONS_USED',rej)
    print('IMPORTANT: rolling zone is a v0.1 PROXY. Original V-shaped 4H/5m zone construction is not yet numerically reproduced.')
    if not all_tr:
        print('NO_TRADES')
        return
    x=pd.DataFrame(all_tr,columns=['date','side','break_time','entry_time','retest_type','sma_ok','r_target','entry','stop','target','exit','gross','reason'])
    rows=[]
    for rt,g in x.groupby('r_target'):
        for cost in COSTS:
            m=metrics(g,cost)
            rows.append((rt,cost,*m.values()))
    cols=['R_TARGET','COST','TRADES','NET','AVG','WIN_RATE','PF','WORST','POS_DATES','DATES','STOP_RATE','SMA_OK_RATE']
    out=pd.DataFrame(rows,columns=cols)
    print('\n===== COST / R SWEEP =====')
    print(out.round(3).to_string(index=False))
    print('\n===== RETEST TYPE @ 1.5R COST0.20 =====')
    g=x[x.r_target==1.5].copy(); g['net']=g.gross-0.20
    if len(g):
        s=(g.groupby('retest_type').agg(TRADES=('net','size'),NET=('net','sum'),AVG=('net','mean'),WIN_RATE=('net',lambda q:(q>0).mean()*100),SMA_OK=('sma_ok','mean')).reset_index())
        s['SMA_OK']=s.SMA_OK*100
        print(s.round(3).to_string(index=False))
    print('\nBENCHMARK FROM SOURCE MATERIAL: 60% win rate / ~1:1.5 RR is a CLAIMED SIMULATION INPUT, not yet an observed strategy result.')
    print('NEXT: inspect whether the retest-character split has signal. Then replace rolling zone proxy with reconstructed V-shaped interaction zones before any PASS/REJECT.')

if __name__=='__main__':
    main()
