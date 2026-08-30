from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT = OUT_DIR / 'v20_entry_extension_cohort.csv'
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_MIN = 52.0
REL_MIN = 1.45


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan

def pct(a,b):
    a=f(a); b=f(b)
    return (b/a-1.0)*100.0 if np.isfinite(a) and a!=0 and np.isfinite(b) else np.nan


def raw_features(bars, ts, ep):
    b=bars.copy(); b['time']=pd.to_datetime(b.time); b=b.sort_values('time')
    q=b[b.time<=pd.Timestamp(ts)].copy()
    if q.empty: return {}
    close=pd.to_numeric(q['close'],errors='coerce')
    cur=f(ep)
    out={}
    for k in [1,2,3,5,10,15]:
        if len(close)>k:
            out[f'runup_{k}m_pct']=pct(close.iloc[-1-k],cur)
        else: out[f'runup_{k}m_pct']=np.nan
    for k in [3,5,10,15]:
        z=close.tail(k)
        if len(z):
            lo=f(z.min()); hi=f(z.max())
            out[f'from_low_{k}m_pct']=pct(lo,cur)
            out[f'below_high_{k}m_pct']=pct(hi,cur)
        else:
            out[f'from_low_{k}m_pct']=np.nan; out[f'below_high_{k}m_pct']=np.nan
    if 'volume' in q.columns:
        vol=pd.to_numeric(q.volume,errors='coerce')
        v3=f(vol.tail(3).mean()) if len(vol)>=3 else np.nan
        vp=f(vol.iloc[:-3].tail(10).mean()) if len(vol)>=13 else np.nan
        out['vol3_vs_prior10']=v3/vp if np.isfinite(v3) and np.isfinite(vp) and vp!=0 else np.nan
    return out


def frame_features(frame, ts, ep):
    q=frame[frame.time<=pd.Timestamp(ts)].copy().sort_values('time')
    if q.empty:return {}
    r=q.iloc[-1]; out={}
    for c in ['rsi','rsi14','rsi_value','rsi_slope','macd_slope','macd_gap','gap','gap_delta','mid_slope8','macd_strength_raw','macd_strength_rel']:
        if c in q.columns: out[c]=f(r.get(c,np.nan))
    close_col='close' if 'close' in q.columns else None
    if close_col:
        cl=pd.to_numeric(q[close_col],errors='coerce')
        cur=f(ep)
        for k in [1,2,3]:
            if len(cl)>k: out[f'runup_{k}x5m_pct']=pct(cl.iloc[-1-k],cur)
        ma20=f(cl.tail(20).mean()) if len(cl)>=5 else np.nan
        out['dist_5m_mean_pct']=pct(ma20,cur)
    # Preserve any Bollinger geometry that already exists without assuming one exact naming convention.
    names=[c for c in q.columns if any(x in c.lower() for x in ['inner_upper','upper_inner','bb_inner','mid'])]
    for c in names:
        if c in out or len(out)>80: continue
        v=f(r.get(c,np.nan))
        if np.isfinite(v):
            out[f'frame_{c}']=v
            if v!=0 and np.isfinite(f(ep)): out[f'dist_{c}_pct']=pct(v,ep)
    return out


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw0=load_data(); raw={n(k):v for k,v in raw0.items()}
    base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw0,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw0,base_cfg))
    frames0=base.build_cfg_frames(raw0,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames0.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(x) for s,x in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw0,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={n(s):h.build_micro(b,cfg) for s,b in raw0.items()}
    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=RAW_MIN,rel_min=REL_MIN)
    tr=multi.simulate_multi(packed,ev20,states,THRESHOLD).copy()
    if tr.empty: print('NO V20 TRADES'); return
    tr['symbol']=tr.symbol.map(n); tr['entry_time']=pd.to_datetime(tr.entry_time)
    tr['net_pct']=pd.to_numeric(tr.pnl_pct,errors='coerce')-FEE_RT_PCT
    tr['result']=np.where(tr.net_pct>0,'WIN','LOSS')
    rows=[]
    for _,r in tr.iterrows():
        sym=n(r.symbol); ts=pd.Timestamp(r.entry_time); ep=f(r.entry_price)
        d=dict(result=r.result,symbol=sym,entry_time=ts,exit_time=r.exit_time,entry_price=ep,net_pct=f(r.net_pct),reason=r.get('reason',''))
        d.update(raw_features(raw[sym],ts,ep)); d.update(frame_features(strength[sym],ts,ep)); rows.append(d)
    z=pd.DataFrame(rows).sort_values('entry_time'); z.to_csv(OUT,index=False)

    metrics=[c for c in ['runup_3m_pct','runup_5m_pct','runup_10m_pct','runup_15m_pct','from_low_5m_pct','from_low_10m_pct','below_high_10m_pct','vol3_vs_prior10','runup_1x5m_pct','runup_2x5m_pct','runup_3x5m_pct','dist_5m_mean_pct','rsi','rsi14','rsi_value','rsi_slope','gap_delta','mid_slope8','macd_strength_raw','macd_strength_rel'] if c in z.columns]
    print('\n=== V20 ENTRY EXTENSION COHORT ===')
    print('Diagnostic only. No filter is applied.')
    print('Question: do losses, especially late/top entries, arrive materially more extended than wins?')
    print(f'TRADES={len(z)} WINS={(z.net_pct>0).sum()} LOSSES={(z.net_pct<=0).sum()} NET={z.net_pct.sum():+.6f}%')
    print('\n=== WIN / LOSS MEANS ===')
    if metrics:
        print(z.groupby('result')[metrics].mean(numeric_only=True).round(6).to_string())
    print('\n=== WORST 8 LOSSES ===')
    show=['symbol','entry_time','net_pct','reason']+[c for c in ['runup_3m_pct','runup_5m_pct','runup_10m_pct','runup_15m_pct','from_low_10m_pct','runup_3x5m_pct','dist_5m_mean_pct','rsi','rsi14','rsi_value','gap_delta','macd_strength_raw','macd_strength_rel'] if c in z.columns]
    print(z.nsmallest(8,'net_pct')[show].to_string(index=False))
    print('\n=== TARGET 950160 2026-08-14 13:55 ===')
    q=z[(z.symbol=='950160')&(z.entry_time==pd.Timestamp('2026-08-14 13:55:00+09:00'))]
    print(q[show].to_string(index=False) if len(q) else 'TARGET NOT FOUND')
    print('\nReading target:')
    print('- Do not set an extension cutoff from this output.')
    print('- First decide whether entry extension is actually a cohort-level loss phenotype or only one famous case.')
    print('- If extension separates losses, next step is a one-dimensional ablation. If it does not, inspect missed earlier transition/source state instead.')
    print('WROTE',OUT)

if __name__=='__main__': main()
