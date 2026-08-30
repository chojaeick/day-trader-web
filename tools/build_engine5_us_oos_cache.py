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
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_slow_turn_prototype as slow
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight, to_5m

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
CACHE_DIR=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE_CACHE=CACHE_DIR/'us_engine5_core.pkl'
PERSIST=CACHE_DIR/'slow_turn_persistence_candidates.csv'
PROV_DIR=CACHE_DIR/'slow_turn_provisional_fast_v2'

# Keep the historical engine key convention for compatibility with the already-built
# core pickle. US tickers therefore appear as e.g. 00SOXL internally; this is only
# an internal key, not a market symbol lookup.
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
        q['time']=pd.to_datetime(q.time,utc=True).dt.tz_convert('America/New_York')
        for c in ['open','high','low','close','volume']:q[c]=pd.to_numeric(q[c],errors='coerce')
        q=q.dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)
        out[key(s)]=q
        print(f'[{i}/{len(syms)}] {s} rows={len(q)} {q.time.min()} -> {q.time.max()}',flush=True)
    con.close(); return out

def build_core(db,syms):
    raw=load_us(db,syms)
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    print('BUILD core frames...',flush=True)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={key(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={key(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(b,cfg) for s,b in raw.items()}
    core=dict(raw=raw,cfg0=cfg0,cfg=cfg,packed=packed,states=states,scored=scored,strength=strength,completed=completed,micros=micros)
    with CORE_CACHE.open('wb') as fh:pickle.dump(core,fh,pickle.HIGHEST_PROTOCOL)
    print('WROTE',CORE_CACHE,flush=True)
    return core


def _rolling_slope_last8(values):
    a=np.asarray(values,dtype=float)
    if len(a)!=8 or not np.isfinite(a).all(): return np.nan
    x=np.arange(8,dtype=float); xc=x-x.mean(); denom=float(np.dot(xc,xc))
    return float(np.dot(xc,a-a.mean())/denom)


def build_minimal_provisional_fast(raw_bars: pd.DataFrame, cfg, completed_strength: pd.DataFrame|None=None) -> pd.DataFrame:
    """O(N) provisional Engine5 features needed by Slow-turn and V-rebound.

    This preserves the indicator formulas while avoiding the old O(N^2) loop that
    re-enriched the entire 5m history for every minute.  No strategy threshold is
    changed; this is only a computationally equivalent feature-building path.
    """
    b=raw_bars.copy().sort_values('time').reset_index(drop=True)
    b['time']=pd.to_datetime(b['time'])
    complete=to_5m(b).sort_values('time').reset_index(drop=True)
    complete['time']=pd.to_datetime(complete['time'])
    c=num(complete['close']).astype(float)

    fast=c.ewm(span=cfg.macd_fast,adjust=False).mean()
    slow_ema=c.ewm(span=cfg.macd_slow,adjust=False).mean()
    macd=fast-slow_ema
    signal=macd.ewm(span=cfg.macd_signal,adjust=False).mean()
    gap=macd-signal

    d=c.diff(); gain=d.clip(lower=0.0); loss=-d.clip(upper=0.0)
    alpha_rsi=1.0/float(cfg.rsi_period)
    ag=gain.ewm(alpha=alpha_rsi,adjust=False,min_periods=cfg.rsi_period).mean()
    al=loss.ewm(alpha=alpha_rsi,adjust=False,min_periods=cfg.rsi_period).mean()
    rs=ag/al.mask(al==0.0,np.nan)
    rsi=100.0-100.0/(1.0+rs)
    mid=c.rolling(cfg.bb_period).mean()

    # Strength baseline is exactly the completed-frame value already built in core.
    baseline_by_time={}
    if completed_strength is not None and len(completed_strength):
        cs=completed_strength.copy(); cs['time']=pd.to_datetime(cs.time)
        if 'strength_baseline' in cs.columns:
            baseline_by_time={pd.Timestamp(t):float(v) if pd.notna(v) else np.nan for t,v in zip(cs.time,cs.strength_baseline)}

    ct=complete['time'].astype('int64').to_numpy()
    close_arr=c.to_numpy(float); fast_arr=fast.to_numpy(float); slow_arr=slow_ema.to_numpy(float)
    macd_arr=macd.to_numpy(float); sig_arr=signal.to_numpy(float); gap_arr=gap.to_numpy(float)
    ag_arr=ag.to_numpy(float); al_arr=al.to_numpy(float); rsi_arr=rsi.to_numpy(float); mid_arr=mid.to_numpy(float)
    af=2.0/(float(cfg.macd_fast)+1.0); asl=2.0/(float(cfg.macd_slow)+1.0); asig=2.0/(float(cfg.macd_signal)+1.0)

    rows=[]
    for r in b.itertuples(index=False):
        ts=pd.Timestamp(r.time); bucket_start=ts.floor('5min'); bucket_end=bucket_start+pd.Timedelta(minutes=5)
        j=int(np.searchsorted(ct,bucket_start.value,side='right')-1)
        if j<20: continue
        px=float(r.close); prev_close=close_arr[j]
        vals=[prev_close,fast_arr[j],slow_arr[j],macd_arr[j],sig_arr[j],gap_arr[j],ag_arr[j],al_arr[j],rsi_arr[j]]
        if not all(np.isfinite(v) for v in vals): continue

        fast_cur=af*px+(1.0-af)*fast_arr[j]
        slow_cur=asl*px+(1.0-asl)*slow_arr[j]
        macd_cur=fast_cur-slow_cur
        signal_cur=asig*macd_cur+(1.0-asig)*sig_arr[j]
        gap_cur=macd_cur-signal_cur
        gap_delta=gap_cur-gap_arr[j]
        macd_slope=macd_cur-macd_arr[j]

        delta=px-prev_close; g=max(delta,0.0); l=max(-delta,0.0)
        ag_cur=alpha_rsi*g+(1.0-alpha_rsi)*ag_arr[j]
        al_cur=alpha_rsi*l+(1.0-alpha_rsi)*al_arr[j]
        rsi_cur=np.nan if al_cur==0.0 else 100.0-100.0/(1.0+ag_cur/al_cur)
        rsi_slope=rsi_cur-rsi_arr[j] if np.isfinite(rsi_cur) else np.nan

        if j<18: continue
        prior19=close_arr[j-18:j+1]
        if len(prior19)!=19 or not np.isfinite(prior19).all(): continue
        mid_cur=float((prior19.sum()+px)/float(cfg.bb_period))
        prior7=mid_arr[j-6:j+1]
        mid_slope=_rolling_slope_last8(np.r_[prior7,mid_cur])

        baseline=baseline_by_time.get(pd.Timestamp(complete.time.iloc[j]),np.nan)
        strength_rel=gap_delta/baseline if np.isfinite(gap_delta) and gap_delta>0 and np.isfinite(baseline) and baseline>0 else np.nan
        rows.append(dict(time=ts,bucket_end=bucket_end,close=px,mid_slope8=mid_slope,
                         gap_delta=gap_delta,macd_slope=macd_slope,rsi_slope=rsi_slope,
                         strength_baseline=baseline,strength_rel=strength_rel))
    return pd.DataFrame(rows)


def load_or_build_fast_provisional(sym, raw_bars, cfg, completed_strength=None):
    PROV_DIR.mkdir(parents=True,exist_ok=True)
    path=PROV_DIR/f'{sym}_slow_v_full.pkl'
    required={'time','bucket_end','close','mid_slope8','gap_delta','macd_slope','rsi_slope','strength_rel'}
    if path.exists():
        with path.open('rb') as fh: pf=pickle.load(fh)
        if required.issubset(pf.columns):
            print(f'FAST PROVISIONAL HIT {sym} rows={len(pf)}',flush=True)
            return pf
    print(f'FAST PROVISIONAL BUILD {sym}...',flush=True)
    pf=build_minimal_provisional_fast(raw_bars,cfg,completed_strength)
    with path.open('wb') as fh: pickle.dump(pf,fh,pickle.HIGHEST_PROTOCOL)
    print(f'FAST PROVISIONAL WROTE {sym} rows={len(pf)} -> {path}',flush=True)
    return pf


def build_base_candidates_fast(raw,cfg,scored,micros,completed=None):
    parts=[]; pf_by_symbol={}
    for i,s in enumerate(raw,1):
        print(f'[{i}/{len(raw)}] {s}',flush=True)
        pf=load_or_build_fast_provisional(s,raw[s],cfg,None if completed is None else completed[s])
        pf_by_symbol[s]=pf
        q=zd.build_candidates(s,pf,micros[s],scored[s])
        if len(q): parts.append(q)
    cand=pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()
    return cand,pf_by_symbol


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); ap.add_argument('--rebuild-core',action='store_true'); a=ap.parse_args()
    syms=[x.strip().upper() for x in a.symbols.split(',') if x.strip()]
    CACHE_DIR.mkdir(parents=True,exist_ok=True)

    if CORE_CACHE.exists() and not a.rebuild_core:
        print('RESUME: loading existing core cache',CORE_CACHE,flush=True)
        with CORE_CACHE.open('rb') as fh: core=pickle.load(fh)
        raw=core['raw']; cfg=core['cfg']; scored=core['scored']; completed=core['completed']; micros=core['micros']
        print(f'CORE CACHE LOADED symbols={len(raw)}. Skipping expensive core rebuild.',flush=True)
    else:
        core=build_core(a.db,syms)
        raw=core['raw']; cfg=core['cfg']; scored=core['scored']; completed=core['completed']; micros=core['micros']

    print('BUILD US slow-turn persistence cache (FAST incremental provisional)...',flush=True)
    cand,pf_by_symbol=build_base_candidates_fast(raw,cfg,scored,micros,completed)
    rows=[]
    if cand.empty:
        pd.DataFrame(rows).to_csv(PERSIST,index=False)
        print('NO BASE SLOW-TURN CANDIDATES',flush=True)
        print('WROTE',PERSIST,'rows=0',flush=True)
        return

    for i,(sym,g) in enumerate(cand.groupby(cand.symbol.astype(str).str.zfill(6)),1):
        pf=pf_by_symbol[sym]; z,m=slow.add_slow_turn_features(pf,micros[sym])
        for _,r in g.iterrows():
            ready=pd.Timestamp(r.ready_time); entry=pd.Timestamp(r.entry_time)
            q5=z[(z.time<=ready)&(z.time>=ready-pd.Timedelta(minutes=6))]; q1=m[(m.time<=entry)&(m.time>=entry-pd.Timedelta(minutes=6))]
            g5=seq_monotonicity(num(q5.gap_delta)) if 'gap_delta' in q5 else np.nan; r5=seq_monotonicity(num(q5.rsi_slope)) if 'rsi_slope' in q5 else np.nan
            g1=seq_monotonicity(num(q1.macd_gap_delta_1m)) if 'macd_gap_delta_1m' in q1 else np.nan; r1=seq_monotonicity(num(q1.rsi_slope_1m)) if 'rsi_slope_1m' in q1 else np.nan
            rows.append(dict(symbol=sym,entry_time=entry,joint5_persistence=min(g5,r5) if np.isfinite(g5) and np.isfinite(r5) else np.nan,joint1_persistence=min(g1,r1) if np.isfinite(g1) and np.isfinite(r1) else np.nan,price_progress_1m_pct=price_progress(m,entry)))
        print(f'  persistence [{i}] {sym} candidates={len(g)}',flush=True)
    pd.DataFrame(rows).to_csv(PERSIST,index=False)
    print('WROTE',PERSIST,'rows=',len(rows),flush=True)
    print('CACHE_READY. No strategy threshold was changed.',flush=True)

if __name__=='__main__':main()
