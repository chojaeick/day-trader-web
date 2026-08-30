from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CASES_SRC = OUT_DIR / 'v21_v_rebound_post_entry_failure_cases.csv'
MIN_SRC = OUT_DIR / 'v21_v_rebound_post_entry_failure_minutes.csv'
OUT = OUT_DIR / 'v21_v_rebound_price_structure_failure.csv'


def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


def main():
    for p in [CASES_SRC, MIN_SRC]:
        if not p.exists(): raise FileNotFoundError(p)
    cases=pd.read_csv(CASES_SRC)
    mins=pd.read_csv(MIN_SRC)
    cases['symbol']=cases.symbol.astype(str).str.zfill(6)
    mins['symbol']=mins.symbol.astype(str).str.zfill(6)
    cases['entry_time']=pd.to_datetime(cases.entry_time)
    mins['entry_time']=pd.to_datetime(mins.entry_time)
    mins['time']=pd.to_datetime(mins.time)

    rows=[]
    for _,c in cases.iterrows():
        sym=c.symbol; et=c.entry_time; ep=f(c.entry_price)
        w=mins[(mins.symbol==sym)&(mins.entry_time==et)].copy().sort_values('time')
        if w.empty: continue
        # These minute files contain px but not low. Use observed px path as a proxy for close-based structural erosion.
        # We deliberately do not invent a true low-based threshold here.
        w['px']=pd.to_numeric(w.px,errors='coerce')
        w['ret_pct']=(w.px/ep-1.0)*100.0
        # Track worsening below-entry excursion and recovery behavior.
        below=w[w.px<ep]
        first_below_time=pd.NaT if below.empty else pd.Timestamp(below.iloc[0].time)
        min_ret=float(w.ret_pct.min()) if len(w) else np.nan
        min_time=pd.Timestamp(w.loc[w.ret_pct.idxmin(),'time']) if len(w) else pd.NaT
        # time to reclaim entry after first going below
        reclaim_time=pd.NaT
        if not pd.isna(first_below_time):
            after=w[w.time>first_below_time]
            r=after[after.px>=ep]
            if not r.empty: reclaim_time=pd.Timestamp(r.iloc[0].time)
        # first 1/2/3 minute excursions
        vals={}
        for m in [1,2,3,4,5]:
            q=w[w.time<=et+pd.Timedelta(minutes=m)]
            vals[f'min_ret_first{m}m_pct']=float(q.ret_pct.min()) if len(q) else np.nan
            vals[f'end_ret_{m}m_pct']=float(q.iloc[-1].ret_pct) if len(q) else np.nan
        rows.append(dict(symbol=sym,entry_time=et,net_pct=f(c.net_pct),reason=c.reason,
                         first_below_time=first_below_time,
                         minutes_to_first_below=((first_below_time-et).total_seconds()/60.0 if not pd.isna(first_below_time) else np.nan),
                         worst_time=min_time,worst_ret_10m_pct=min_ret,
                         reclaim_time=reclaim_time,
                         reclaim_minutes=((reclaim_time-et).total_seconds()/60.0 if not pd.isna(reclaim_time) else np.nan),
                         mfe_10m_pct=f(c.mfe_10m_pct),mae_10m_pct=f(c.mae_10m_pct),**vals))

    out=pd.DataFrame(rows).sort_values('entry_time')
    out.to_csv(OUT,index=False)
    print('\n=== V REBOUND PRICE-STRUCTURE FAILURE DIAGNOSTIC ===')
    print('Descriptive only. Uses observed 1m price path from the existing diagnostic; no exit rule changed.')
    show=['symbol','entry_time','net_pct','reason','minutes_to_first_below','worst_ret_10m_pct','reclaim_minutes',
          'min_ret_first1m_pct','min_ret_first2m_pct','min_ret_first3m_pct','min_ret_first4m_pct','min_ret_first5m_pct',
          'end_ret_2m_pct','end_ret_5m_pct','mfe_10m_pct','mae_10m_pct']
    print(out[show].to_string(index=False))
    print('\nReading target:')
    print('- If losers show immediate/deep below-entry erosion while the large winner does not, price structure should outrank momentum waiting.')
    print('- If one loser briefly dips then recovers, do not freeze a fixed percent stop from this tiny sample.')
    print('- This file is a structural diagnostic only; no threshold is selected here.')
    print('WROTE',OUT)

if __name__=='__main__': main()
