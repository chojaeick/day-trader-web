from __future__ import annotations

import argparse
import pickle
import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_slow_turn_regime_integrated as sri
import tools.validate_engine5_slow_turn_prototype as slow
import tools.diagnose_v20_transition_structure_targets as st
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
CACHE_DIR=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE_CACHE=CACHE_DIR/'us_engine5_core.pkl'
PERSIST=CACHE_DIR/'slow_turn_persistence_candidates.csv'


def key(s): return str(s).zfill(6)

def num(x): return pd.to_numeric(x,errors='coerce')

def seq_monotonicity(vals):
    s=pd.Series(vals,dtype='float64').dropna()
    if len(s)<2:return np.nan
    d=s.diff().dropna(); up=float(d[d>0].sum()) if (d>0).any() else 0.; dn=float(-d[d<0].sum()) if (d<0).any() else 0.
    return up/(up+dn) if up+dn>0 else np.nan

def price_progress(m,entry):
    q=m[(m.time<=entry)&(m.time>=entry-pd.Timedelta(minutes=6))]
    c=num(q.close).dropna()
    return float(c.iloc[-1]/c.iloc[0]-1.)*100. if len(c)>=2 and c.iloc[0]>0 else np.nan

def load_us(db,syms):
    con=sqlite3.connect(db); out={}
    for i,s in enumerate(syms,1):
        q=pd.read_sql_query("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date,et_time",con,params=(s,))
        if q.empty: print(f'[{i}/{len(syms)}] {s} EMPTY',flush=True); continue
        q=q.rename(columns={'et_time':'time'})
        # SQLite stores ET with seasonal offsets (-05:00 / -04:00). Parse through UTC so
        # pandas gets one stable datetime64 dtype across the DST boundary, then convert to
        # America/New_York. The engine only needs a consistent causal timeline.
        q['time']=pd.to_datetime(q.time,utc=True).dt.tz_convert('America/New_York')
        for c in ['open','high','low','close','volume']:q[c]=pd.to_numeric(q[c],errors='coerce')
        q=q.dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)
        out[key(s)]=q
        print(f'[{i}/{len(syms)}] {s} rows={len(q)} {q.time.min()} -> {q.time.max()}',flush=True)
    con.close(); return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); a=ap.parse_args()
    syms=[x.strip().upper() for x in a.symbols.split(',') if x.strip()]
    CACHE_DIR.mkdir(parents=True,exist_ok=True)
    raw=load_us(a.db,syms)
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    print('BUILD core frames...',flush=True)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={key(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={key(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:rt.add_strength_columns(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(b,cfg) for s,b in raw.items()}
    with CORE_CACHE.open('wb') as fh:pickle.dump(dict(raw=raw,cfg0=cfg0,cfg=cfg,packed=packed,states=states,scored=scored,strength=strength,completed=completed,micros=micros),fh,pickle.HIGHEST_PROTOCOL)
    print('WROTE',CORE_CACHE,flush=True)

    print('BUILD US slow-turn persistence cache...',flush=True)
    cand=sri.reconstruct_base_candidates(raw,cfg,scored,completed,micros)
    rows=[]
    for i,(sym,g) in enumerate(cand.groupby(cand.symbol.astype(str).str.zfill(6)),1):
        pf,_=st.load_or_build_cache(sym,raw[sym],cfg,completed[sym]); z,m=slow.add_slow_turn_features(pf,micros[sym])
        for _,r in g.iterrows():
            ready=pd.Timestamp(r.ready_time); entry=pd.Timestamp(r.entry_time)
            q5=z[(z.time<=ready)&(z.time>=ready-pd.Timedelta(minutes=6))]; q1=m[(m.time<=entry)&(m.time>=entry-pd.Timedelta(minutes=6))]
            g5=seq_monotonicity(num(q5.gap_delta)) if 'gap_delta' in q5 else np.nan; r5=seq_monotonicity(num(q5.rsi_slope)) if 'rsi_slope' in q5 else np.nan
            g1=seq_monotonicity(num(q1.macd_gap_delta_1m)) if 'macd_gap_delta_1m' in q1 else np.nan; r1=seq_monotonicity(num(q1.rsi_slope_1m)) if 'rsi_slope_1m' in q1 else np.nan
            rows.append(dict(symbol=sym,entry_time=entry,joint5_persistence=min(g5,r5) if np.isfinite(g5) and np.isfinite(r5) else np.nan,joint1_persistence=min(g1,r1) if np.isfinite(g1) and np.isfinite(r1) else np.nan,price_progress_1m_pct=price_progress(m,entry)))
        print(f'  persistence [{i}] {sym} candidates={len(g)}',flush=True)
    pd.DataFrame(rows).to_csv(PERSIST,index=False)
    print('WROTE',PERSIST,'rows=',len(rows))
    print('CACHE_READY. No strategy threshold was changed.')
if __name__=='__main__':main()