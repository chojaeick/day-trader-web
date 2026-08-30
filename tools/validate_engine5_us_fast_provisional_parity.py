from __future__ import annotations

"""Focused correctness check for the US O(N) provisional cache.

This is NOT a strategy backtest and changes no thresholds.  It compares the fast
US provisional implementation against the original Engine5 causal provisional
builder on a deliberately small slice, so the old O(N^2) implementation remains
tractable.

For each selected symbol/day it rebuilds enough warm-up history, compares the
same minute timestamps during the target regular session, and reports numerical
error for the structural fields consumed by Slow-turn/V-rebound.
"""

import argparse
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_v20_regime_transition as rt

CACHE_DIR = Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE = CACHE_DIR / 'us_engine5_core.pkl'
OUT = CACHE_DIR / 'us_fast_provisional_parity.csv'
FIELDS = ['mid_slope8','gap_delta','rsi_slope','macd_slope','strength_rel']


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x, errors='coerce')


def pick_day(b: pd.DataFrame, day_offset: int):
    days = sorted(pd.to_datetime(b.time).dt.date.unique())
    if not days: raise RuntimeError('no trading days')
    idx = max(0, min(len(days)-1, len(days)-1-day_offset))
    return days[idx], days


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbols', default='SOXL,AMD')
    ap.add_argument('--day-offset', type=int, default=10, help='0=last cached day; default avoids edge-only choice')
    ap.add_argument('--warmup-days', type=int, default=3)
    a=ap.parse_args()
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh: d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; completed=d['completed']

    print('=== US FAST PROVISIONAL PARITY ===', flush=True)
    print('NO THRESHOLD CHANGES. Original causal builder vs fast O(N) builder.', flush=True)
    rows=[]
    for ticker in [x.strip().upper() for x in a.symbols.split(',') if x.strip()]:
        s=n(ticker)
        if s not in raw:
            print(f'{ticker}: MISSING', flush=True); continue
        b=raw[s].copy().sort_values('time').reset_index(drop=True)
        b['time']=pd.to_datetime(b.time)
        target, days=pick_day(b,a.day_offset)
        pos=days.index(target); start_day=days[max(0,pos-a.warmup_days)]
        sl=b[(b.time.dt.date>=start_day)&(b.time.dt.date<=target)].copy().reset_index(drop=True)
        # Original causal path; deliberately only a few days.
        print(f'{ticker}: target={target} warmup_start={start_day} rows={len(sl)} ORIGINAL...', flush=True)
        old=rt.build_provisional_5m(sl,cfg)
        # add_provisional_strength expects the matching completed strength frame; use full cached
        # completed history because baseline is a prior-completed-bar quantity.
        old=rt.add_provisional_strength(old,completed[s])
        print(f'{ticker}: FAST...', flush=True)
        fast=uscache.build_minimal_provisional_fast(sl,cfg,completed[s])
        old['time']=pd.to_datetime(old.time); fast['time']=pd.to_datetime(fast.time)
        old=old[old.time.dt.date==target].copy(); fast=fast[fast.time.dt.date==target].copy()
        cols=['time']+[c for c in FIELDS if c in old.columns]
        z=old[cols].merge(fast[['time']+[c for c in FIELDS if c in fast.columns]],on='time',how='outer',suffixes=('_old','_fast'),indicator=True)
        matched=int((z._merge=='both').sum()); old_only=int((z._merge=='left_only').sum()); fast_only=int((z._merge=='right_only').sum())
        print(f'{ticker}: timestamps matched={matched} old_only={old_only} fast_only={fast_only}', flush=True)
        for f in FIELDS:
            oc=f'{f}_old'; fc=f'{f}_fast'
            if oc not in z or fc not in z: continue
            x=num(z[oc]); y=num(z[fc]); mask=np.isfinite(x)&np.isfinite(y)
            diff=(x[mask]-y[mask]).abs()
            max_abs=float(diff.max()) if len(diff) else np.nan
            mean_abs=float(diff.mean()) if len(diff) else np.nan
            denom=np.maximum(np.maximum(x[mask].abs(),y[mask].abs()),1e-12)
            max_rel=float((diff/denom).max()) if len(diff) else np.nan
            rows.append(dict(symbol=ticker,day=str(target),field=f,n=int(mask.sum()),max_abs=max_abs,mean_abs=mean_abs,max_rel=max_rel,matched=matched,old_only=old_only,fast_only=fast_only))
            print(f'  {f}: n={int(mask.sum())} max_abs={max_abs:.12g} mean_abs={mean_abs:.12g} max_rel={max_rel:.12g}', flush=True)

    out=pd.DataFrame(rows); out.to_csv(OUT,index=False)
    print('\n=== VERDICT INPUT ===')
    if len(out):
        print(out[['symbol','day','field','n','max_abs','mean_abs','max_rel']].to_string(index=False))
    print('WROTE',OUT)
    print('Interpretation: any material gap_delta/rsi_slope/macd_slope/mid_slope8 mismatch means current US strategy results must not be trusted until the fast cache is corrected.')

if __name__=='__main__': main()
