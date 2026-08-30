from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
import tools.validate_engine5_v21_v_rebound_reaccel as ra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as mp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_CASES = OUT_DIR / 'v21_v_rebound_initial_structure_cohort.csv'
OUT_GROUP = OUT_DIR / 'v21_v_rebound_initial_structure_group.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
FEE_RT_PCT = 0.25


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y=float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def pct(a,b):
    a=f(a); b=f(b)
    return (b/a-1.0)*100.0 if np.isfinite(a) and a != 0 and np.isfinite(b) else np.nan


def first_reclaim_after_below(w, ep):
    below = w['close'] < ep
    if not below.any():
        return np.nan
    first = int(np.flatnonzero(below.to_numpy())[0])
    after = w.iloc[first+1:]
    hit = after[after['close'] >= ep]
    if hit.empty:
        return np.nan
    return (pd.Timestamp(hit.iloc[0].time) - pd.Timestamp(w.iloc[0].time)).total_seconds()/60.0


def stat(label,g):
    net=pd.to_numeric(g.net_pct,errors='coerce').dropna()
    return dict(label=label,trades=len(net),wins=int((net>0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum=float(net.sum()) if len(net) else 0.0,
                first3_low_ret_mean=float(pd.to_numeric(g.first3_low_ret_pct,errors='coerce').mean()) if len(g) else np.nan,
                first3_close_ret_mean=float(pd.to_numeric(g.first3_close_ret_pct,errors='coerce').mean()) if len(g) else np.nan,
                reclaim_by5_rate=float(pd.to_numeric(g.reclaim_by5,errors='coerce').mean()*100) if len(g) else np.nan,
                never_below_rate=float(pd.to_numeric(g.never_below_5m,errors='coerce').mean()*100) if len(g) else np.nan)


def main():
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}

    allc=[]; features={}
    for sym,bars in raw.items():
        pf,m=old.load_cache(sym,bars,cfg,completed[sym])
        z=sm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        z['time']=pd.to_datetime(z.time)
        features[sym]=z
        c=sm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): allc.append(c)

    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO CANDIDATES'); return
    cand=ra.add_pullback_reaccel(cand,features)
    cand=mp.add_preservation(cand,features)

    # Broader diagnostic cohort than the selected-3: keep the same V architecture,
    # stop/reaccel/volume/RSI-pullback requirements, but do NOT impose GAP_KEEP>=0.9.
    q=cand[(cand.stop_dist_pct<=STOP_CAP)&cand.reaccel_pass&
           (pd.to_numeric(cand.volume_accel,errors='coerce')>=VOL_MIN)&
           cand.rsi_positive_all].copy()
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first').reset_index(drop=True)
    if q.empty:
        print('NO COHORT'); return

    rows=[]
    for _,r in q.iterrows():
        sym=n(r.symbol); ts=pd.Timestamp(r.time); ep=f(r.price)
        # Simulate this V candidate alone so its outcome is not masked by other candidate interactions.
        vev,meta,qsel=sm.select(pd.DataFrame([r]),RAW_MIN,LEG_MIN,STOP_CAP,None)
        tr=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        if tr.empty:
            continue
        t=tr.iloc[0]
        net=f(t.pnl_pct)-FEE_RT_PCT

        z=features[sym].copy()
        close_col='px' if 'px' in z.columns else 'close'
        low_col='lo' if 'lo' in z.columns else 'low'
        zz=z[(z.time>=ts)&(z.time<=ts+pd.Timedelta(minutes=5))].copy().sort_values('time')
        if zz.empty: continue
        zz['close']=pd.to_numeric(zz[close_col],errors='coerce')
        zz['low']=pd.to_numeric(zz[low_col],errors='coerce')
        w3=zz[zz.time<=ts+pd.Timedelta(minutes=3)]
        w5=zz[zz.time<=ts+pd.Timedelta(minutes=5)]
        first3_low=pct(ep,w3.low.min())
        first3_close=pct(ep,w3.close.min())
        first5_low=pct(ep,w5.low.min())
        never_below=bool((w5.low>=ep).all())
        reclaim_min=first_reclaim_after_below(w5,ep)
        reclaim_by5=bool(np.isfinite(reclaim_min) and reclaim_min<=5.0)
        first_below_min=np.nan
        hit=w5[w5.low<ep]
        if len(hit): first_below_min=(pd.Timestamp(hit.iloc[0].time)-ts).total_seconds()/60.0

        rows.append(dict(
            result='WIN' if net>0 else 'LOSS',symbol=sym,entry_time=ts,net_pct=net,
            reason=t.get('reason',''),gap_keep_ratio=f(r.gap_keep_ratio),volume_accel=f(r.volume_accel),
            stop_dist_pct=f(r.stop_dist_pct),pullback_minutes=f(r.pullback_minutes),
            first_below_minutes=first_below_min,first3_low_ret_pct=first3_low,
            first3_close_ret_pct=first3_close,first5_low_ret_pct=first5_low,
            reclaim_minutes=reclaim_min,reclaim_by5=reclaim_by5,never_below_5m=never_below,
        ))

    x=pd.DataFrame(rows).sort_values('entry_time')
    if x.empty:
        print('NO SIMULATED CASES'); return
    group=pd.DataFrame([stat('LOSS',x[x.net_pct<=0]),stat('WIN',x[x.net_pct>0]),stat('ALL',x)])
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    x.to_csv(OUT_CASES,index=False); group.to_csv(OUT_GROUP,index=False)

    print('\n=== V-REBOUND INITIAL PRICE-STRUCTURE COHORT ===')
    print('Diagnostic only. Entry/exit rules unchanged.')
    print('Cohort: STOP<=2, REACCEL, VOL>=1.0, RSI positive through pullback; GAP_KEEP is descriptive, not filtered.')
    print('\n=== GROUP SUMMARY ===')
    print(group.to_string(index=False))
    print('\n=== CASES ===')
    cols=['result','symbol','entry_time','net_pct','gap_keep_ratio','stop_dist_pct','first_below_minutes',
          'first3_low_ret_pct','first3_close_ret_pct','first5_low_ret_pct','reclaim_minutes','reclaim_by5','never_below_5m','reason']
    print(x[cols].to_string(index=False))
    print('\nReading target:')
    print('- Look for losers with deep early low erosion + no quick reclaim versus winners that stay above entry or reclaim quickly.')
    print('- Do NOT choose a fixed percent stop from this run alone.')
    print('- If separation is weak, keep Higher-Low structural stop and abandon extra early-price exit complexity.')
    print('WROTE',OUT_CASES)
    print('WROTE',OUT_GROUP)

if __name__=='__main__': main()
