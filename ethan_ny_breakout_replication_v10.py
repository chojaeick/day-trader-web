#!/usr/bin/env python3
"""ETHAN NY BREAKOUT REPLICATION v1.0

Purpose
- Source-locked architecture/replication probe, NOT a promotion/rejection backtest.
- Uses QQQ as a proxy when NQ/MNQ is unavailable in historical_minute_bars.
- 5m V-reaction levels -> nearby-level clustering -> space filter -> 50 SMA alignment
  -> candle-close breakout -> wait for retest -> 2R overextension cancellation -> entry.
- Optional 4H obstacle diagnostic is reported separately.

Important
- Source material is discretionary for exact V/pivot/zone widths and retest-speed thresholds.
- Every numerical translation below is explicitly an ENGINEERING HYPOTHESIS.
- A failed result rejects only this machine translation, never the original Ethan strategy.
- DB READ ONLY / NO AUTO ORDER.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "daytrader.db"
SYMBOL = "QQQ"
COSTS = [0.20, 0.25, 0.30]

# ENGINEERING HYPOTHESES for discretionary source concepts.
PIVOT_LEFT = 2
PIVOT_RIGHT = 2              # causal confirmation delay
CLUSTER_ATR_FRAC = 0.30      # nearby V pivots treated as same level
MIN_CLUSTER_TOUCHES = 1      # source permits even one strong V reaction
MIN_SPACE_R = 2.0
BREAK_CLOSE_ATR = 0.00       # source says close beyond; no invented decisive buffer in baseline
RETEST_TOL_ATR = 0.20
MAX_WAIT_5M = 12             # 60 minutes after breakout
SLOW_MAX_BODY_ATR = 0.45
SLOW_MIN_BARS = 2
MOMENTUM_MIN_BODY_ATR = 0.70
FOUR_HOUR_WALL_ATR = 0.50


@dataclass
class Setup:
    date: str
    direction: str
    break_i: int
    entry_i: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    level: float
    risk: float
    retest_type: str
    sma_ok: bool
    space_r: float
    h4_wall_ahead: bool


def load_1m(symbol: str) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(DB)
    q = """
    SELECT trade_date,et_time,open,high,low,close,volume
    FROM historical_minute_bars
    WHERE symbol=? AND interval_min=1 AND session='REGULAR'
    ORDER BY trade_date,et_time
    """
    x = pd.read_sql_query(q, con, params=[symbol])
    con.close()
    out = {}
    for d,z in x.groupby('trade_date', sort=True):
        if len(z) >= 300:
            out[str(d)] = z.reset_index(drop=True)
    return out


def to_5m(z: pd.DataFrame) -> pd.DataFrame:
    q = z.copy().reset_index(drop=True)
    q['bucket'] = np.arange(len(q)) // 5
    b = (q.groupby('bucket', sort=True)
          .agg(time=('et_time','last'), open=('open','first'), high=('high','max'),
               low=('low','min'), close=('close','last'), volume=('volume','sum'))
          .reset_index(drop=True))
    return b


def atr14(z: pd.DataFrame) -> pd.Series:
    h=z.high.astype(float); l=z.low.astype(float); c=z.close.astype(float)
    pc=c.shift(1)
    tr=pd.concat([(h-l).abs(),(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(14,min_periods=5).mean()


def prep5(z: pd.DataFrame) -> pd.DataFrame:
    q=z.copy()
    q['atr']=atr14(q)
    q['sma50']=q.close.astype(float).rolling(50,min_periods=20).mean()
    q['sma50_slope']=q.sma50-q.sma50.shift(3)
    q['body']=(q.close.astype(float)-q.open.astype(float)).abs()
    q['body_atr']=q.body/q.atr.replace(0,np.nan)
    return q


def confirmed_pivots(q: pd.DataFrame):
    """Return causal V-shaped pivot candidates.

    Pivot is *known* only PIVOT_RIGHT bars later. This is an engineering translation,
    not a claim about Ethan's exact visual pivot rule.
    """
    lows=q.low.astype(float).to_numpy(); highs=q.high.astype(float).to_numpy()
    out=[]
    n=len(q)
    for p in range(PIVOT_LEFT, n-PIVOT_RIGHT):
        lo=lows[p]; hi=highs[p]
        is_low=lo <= np.nanmin(lows[p-PIVOT_LEFT:p+PIVOT_RIGHT+1])
        is_high=hi >= np.nanmax(highs[p-PIVOT_LEFT:p+PIVOT_RIGHT+1])
        confirm_i=p+PIVOT_RIGHT
        if is_low:
            out.append((confirm_i,p,'SUPPORT',lo))
        if is_high:
            out.append((confirm_i,p,'RESISTANCE',hi))
    return out


def live_levels(q: pd.DataFrame):
    """Build nearby-price clusters causally from confirmed V pivots."""
    piv=confirmed_pivots(q)
    levels_by_i=[]
    clusters=[]
    k=0
    for i in range(len(q)):
        while k<len(piv) and piv[k][0] <= i:
            _,p,typ,px=piv[k]
            a=float(q.loc[i,'atr']) if pd.notna(q.loc[i,'atr']) else 0.0
            tol=max(a*CLUSTER_ATR_FRAC, abs(px)*0.0005)
            match=None
            for c in clusters:
                if c['type']==typ and abs(c['price']-px)<=tol:
                    match=c; break
            if match is None:
                clusters.append({'type':typ,'price':float(px),'touches':1,'last_pivot':p})
            else:
                n=match['touches']
                match['price']=(match['price']*n+px)/(n+1)
                match['touches']=n+1; match['last_pivot']=p
            k+=1
        levels_by_i.append([dict(x) for x in clusters if x['touches']>=MIN_CLUSTER_TOUCHES])
    return levels_by_i


def nearest_opposing(levels, direction, entry):
    if direction=='LONG':
        vals=[x['price'] for x in levels if x['type']=='RESISTANCE' and x['price']>entry]
        return min(vals) if vals else None
    vals=[x['price'] for x in levels if x['type']=='SUPPORT' and x['price']<entry]
    return max(vals) if vals else None


def four_hour_wall(q5: pd.DataFrame, i: int, direction: str, entry: float, risk: float) -> bool:
    # Diagnostic approximation only: aggregate all completed 5m bars into completed 4H blocks.
    hist=q5.iloc[:i+1].copy().reset_index(drop=True)
    if len(hist)<48:
        return False
    hist['blk']=np.arange(len(hist))//48
    h4=(hist.groupby('blk').agg(high=('high','max'),low=('low','min'),close=('close','last')).reset_index(drop=True))
    if len(h4)<2:
        return False
    prev=h4.iloc[:-1]
    if direction=='LONG':
        walls=prev.high.astype(float)
        ahead=walls[(walls>entry)&(walls-entry <= max(2*risk, FOUR_HOUR_WALL_ATR*risk*2))]
    else:
        walls=prev.low.astype(float)
        ahead=walls[(walls<entry)&(entry-walls <= max(2*risk, FOUR_HOUR_WALL_ATR*risk*2))]
    return len(ahead)>0


def detect_day(day: str, raw1: pd.DataFrame):
    q=prep5(to_5m(raw1)); levels_map=live_levels(q)
    setups=[]; diag={'breaks':0,'space_reject':0,'overext':0,'no_retest':0,'sma_reject':0}
    # 09:30-10:30 ET main decision window. et_time is assumed HH:MM-like or ISO-like text.
    for i in range(50, len(q)-2):
        t=str(q.loc[i,'time'])
        # tolerate different stored representations by extracting HHMM digits if possible
        digits=''.join(ch for ch in t if ch.isdigit())
        hhmm=None
        if len(digits)>=4:
            hhmm=digits[-4:]
            if len(digits)>=6: hhmm=digits[-6:-2]
        # If parsing cannot confidently identify clock, keep bar; source DB ordering still causal.
        if hhmm and not ('0930' <= hhmm <= '1030'):
            continue
        atr=float(q.loc[i,'atr']) if pd.notna(q.loc[i,'atr']) else 0.0
        if atr<=0: continue
        close=float(q.loc[i,'close']); prev_close=float(q.loc[i-1,'close'])
        levels=levels_map[i-1]
        candidates=[]
        for lv in levels:
            px=float(lv['price'])
            if lv['type']=='RESISTANCE' and prev_close<=px and close>px+BREAK_CLOSE_ATR*atr:
                candidates.append(('LONG',lv))
            elif lv['type']=='SUPPORT' and prev_close>=px and close<px-BREAK_CLOSE_ATR*atr:
                candidates.append(('SHORT',lv))
        for direction,lv in candidates:
            diag['breaks']+=1
            level=float(lv['price'])
            # Stop uses opposite extreme of breakout candle / recent local swing approximation.
            if direction=='LONG':
                stop=float(q.iloc[max(0,i-3):i+1].low.astype(float).min())
                risk=level-stop
            else:
                stop=float(q.iloc[max(0,i-3):i+1].high.astype(float).max())
                risk=stop-level
            if risk<=0: continue
            opp=nearest_opposing(levels,direction,level)
            if opp is None:
                space_r=99.0
            else:
                space=(opp-level) if direction=='LONG' else (level-opp)
                space_r=space/risk if risk else 0
            if space_r<MIN_SPACE_R:
                diag['space_reject']+=1; continue
            sma=float(q.loc[i,'sma50']) if pd.notna(q.loc[i,'sma50']) else np.nan
            slope=float(q.loc[i,'sma50_slope']) if pd.notna(q.loc[i,'sma50_slope']) else 0
            sma_ok=(direction=='LONG' and close>=sma and slope>=0) or (direction=='SHORT' and close<=sma and slope<=0)
            if not sma_ok:
                diag['sma_reject']+=1; continue
            tp1=level+risk if direction=='LONG' else level-risk
            tp2=level+2*risk if direction=='LONG' else level-2*risk
            entry_i=None; retest_type=None; over=False
            end=min(len(q)-1,i+MAX_WAIT_5M)
            for j in range(i+1,end+1):
                row=q.iloc[j]
                if direction=='LONG' and float(row.high)>=tp2:
                    over=True; break
                if direction=='SHORT' and float(row.low)<=tp2:
                    over=True; break
                tol=RETEST_TOL_ATR*(float(row.atr) if pd.notna(row.atr) else atr)
                touched=(direction=='LONG' and float(row.low)<=level+tol) or (direction=='SHORT' and float(row.high)>=level-tol)
                if not touched: continue
                prevs=q.iloc[max(i+1,j-3):j+1]
                med=float(prevs.body_atr.median()) if len(prevs) else 99
                if len(prevs)>=SLOW_MIN_BARS and med<=SLOW_MAX_BODY_ATR:
                    retest_type='SLOW_DIRECT'; entry_i=j
                elif float(row.body_atr) >= MOMENTUM_MIN_BODY_ATR:
                    # Simple 5m rejection proxy only; true source can drill to 1m.
                    if direction=='LONG':
                        reject=float(row.close)>level and float(row.close)>float(row.open)
                    else:
                        reject=float(row.close)<level and float(row.close)<float(row.open)
                    if reject:
                        retest_type='MOMENTUM_REJECTION'; entry_i=j
                else:
                    # neutral return: require a close back in breakout direction
                    if direction=='LONG': reject=float(row.close)>level
                    else: reject=float(row.close)<level
                    if reject:
                        retest_type='NEUTRAL_REJECTION'; entry_i=j
                if entry_i is not None: break
            if over:
                diag['overext']+=1; continue
            if entry_i is None:
                diag['no_retest']+=1; continue
            wall=four_hour_wall(q,entry_i,direction,level,risk)
            setups.append(Setup(day,direction,i,entry_i,level,stop,tp1,tp2,level,risk,retest_type,sma_ok,space_r,wall))
    return q,setups,diag


def simulate(q: pd.DataFrame, s: Setup, mode: str='FUNDED'):
    entry=s.entry; stop=s.stop; tp1=s.tp1; tp2=s.tp2
    be=False
    for j in range(s.entry_i, len(q)):
        r=q.iloc[j]; lo=float(r.low); hi=float(r.high)
        # conservative same-bar ordering
        if s.direction=='LONG':
            if lo<=stop:
                return -1.0,'STOP'
            if mode=='EVALUATION' and hi>=tp1:
                return 1.0,'TP1'
            if mode=='FUNDED' and not be and hi>=tp1:
                be=True; stop=entry
            if mode=='FUNDED' and hi>=tp2:
                return 2.0,'TP2'
            if be and lo<=entry:
                return 0.0,'BE'
        else:
            if hi>=stop:
                return -1.0,'STOP'
            if mode=='EVALUATION' and lo<=tp1:
                return 1.0,'TP1'
            if mode=='FUNDED' and not be and lo<=tp1:
                be=True; stop=entry
            if mode=='FUNDED' and lo<=tp2:
                return 2.0,'TP2'
            if be and hi>=entry:
                return 0.0,'BE'
    # source is intraday; unresolved trade force-flat at EOD in R units
    last=float(q.iloc[-1].close)
    pnl=(last-entry) if s.direction=='LONG' else (entry-last)
    return pnl/s.risk,'EOD'


def summary(rows: pd.DataFrame, mode: str):
    if rows.empty:
        return {'MODE':mode,'TRADES':0,'WIN_RATE':0,'BE_RATE':0,'AVG_R':0,'PF_R':0,'WALL_AHEAD':0}
    x=rows[rows['mode']==mode].copy()
    pos=x.loc[x.r>0,'r'].sum(); neg=-x.loc[x.r<0,'r'].sum()
    return {'MODE':mode,'TRADES':len(x),'WIN_RATE':100*(x.r>0).mean(),'BE_RATE':100*(x.r==0).mean(),
            'AVG_R':x.r.mean(),'PF_R':(pos/neg if neg>0 else np.inf),'WALL_AHEAD':100*x.h4_wall.mean()}


def main():
    data=load_1m(SYMBOL)
    print('===== ETHAN NY BREAKOUT REPLICATION v1.0 =====')
    print('SOURCE-LOCKED CORE / QQQ PROXY / 5M / CAUSAL / DB READ ONLY / NO AUTO ORDER')
    print('DAYS',len(data))
    print('ENGINEERING_HYPOTHESES pivot',PIVOT_LEFT,PIVOT_RIGHT,'cluster_atr',CLUSTER_ATR_FRAC,
          'retest_tol_atr',RETEST_TOL_ATR,'slow_body_atr',SLOW_MAX_BODY_ATR)
    all_setups=[]; total_diag={'breaks':0,'space_reject':0,'overext':0,'no_retest':0,'sma_reject':0}; qmap={}
    for d,raw in data.items():
        q,ss,diag=detect_day(d,raw); qmap[d]=q; all_setups.extend(ss)
        for k,v in diag.items(): total_diag[k]+=v
    print('DIAGNOSTIC',total_diag)
    print('SETUPS',len(all_setups))
    rows=[]
    for s in all_setups:
        for mode in ('EVALUATION','FUNDED'):
            r,reason=simulate(qmap[s.date],s,mode)
            rows.append({'date':s.date,'direction':s.direction,'mode':mode,'r':r,'reason':reason,
                         'retest_type':s.retest_type,'space_r':s.space_r,'h4_wall':s.h4_wall_ahead})
    x=pd.DataFrame(rows)
    print('\n===== R-METRICS BEFORE COST TRANSLATION =====')
    for mode in ('EVALUATION','FUNDED'):
        print(summary(x,mode))
    if not x.empty:
        print('\n===== RETEST TYPE / FUNDED =====')
        y=x[x.mode=='FUNDED'].groupby('retest_type').agg(TRADES=('r','size'),AVG_R=('r','mean'),WIN_RATE=('r',lambda s:100*(s>0).mean()),BE_RATE=('r',lambda s:100*(s==0).mean())).round(3)
        print(y.to_string())
        print('\n===== 4H WALL DIAGNOSTIC / FUNDED =====')
        z=x[x.mode=='FUNDED'].groupby('h4_wall').agg(TRADES=('r','size'),AVG_R=('r','mean'),WIN_RATE=('r',lambda s:100*(s>0).mean())).round(3)
        print(z.to_string())
    print('\nINTERPRETATION RULE: This is a replication probe. A weak result means the numerical translation needs review; it does NOT reject Ethan\'s original strategy.')
    print('NEXT AFTER DB BACKFILL: compare V-level examples visually, then freeze detector before temporal OOS.')

if __name__=='__main__':
    main()
