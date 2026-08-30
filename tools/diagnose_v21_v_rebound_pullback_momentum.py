from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
import tools.validate_engine5_v21_v_rebound_reaccel as ra
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

RAW_MIN=30.0
LEG_MIN=2.0
STOP_CAP=2.0
VOL_MIN=1.5


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


def main():
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    frames=v10.base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}

    allc=[]; feature_by_symbol={}
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True)
        pf,m=old.load_cache(sym,bars,cfg,completed[sym])
        z=sm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        feature_by_symbol[sym]=z
        c=sm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): allc.append(c)

    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO V CANDIDATES'); return
    cand=ra.add_pullback_reaccel(cand,feature_by_symbol)
    q=cand[(cand.stop_dist_pct<=STOP_CAP)&(pd.to_numeric(cand.volume_accel,errors='coerce')>=VOL_MIN)&cand.reaccel_pass].copy()
    if q.empty:
        print('NO ELIGIBLE REACCEL CANDIDATES'); return
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first').reset_index(drop=True)

    rows=[]
    print('\n=== PULLBACK MOMENTUM PRESERVATION ===')
    print('Eligible path fixed: RAW30 LEG2.0 STOP<=2.0 VOL>=1.5 REACCEL=ON')
    print('Measure only first_rebound_high_time -> entry_time. No future bars used.')

    for _,r in q.iterrows():
        sym=n(r.symbol); z=feature_by_symbol[sym].copy(); z['time']=pd.to_datetime(z.time)
        t0=pd.Timestamp(r.first_rebound_high_time); t1=pd.Timestamp(r.time)
        w=z[(z.time>=t0)&(z.time<=t1)].copy()
        gd=pd.to_numeric(w.gap_delta,errors='coerce')
        rs=pd.to_numeric(w.rsi_slope,errors='coerce')
        px=pd.to_numeric(w.px,errors='coerce')
        if w.empty: continue
        high_gap=f(gd.iloc[0]); entry_gap=f(gd.iloc[-1]); min_gap=f(gd.min())
        high_rsi=f(rs.iloc[0]); entry_rsi=f(rs.iloc[-1]); min_rsi=f(rs.min())
        gap_drop=(high_gap-min_gap) if np.isfinite(high_gap) and np.isfinite(min_gap) else np.nan
        rsi_drop=(high_rsi-min_rsi) if np.isfinite(high_rsi) and np.isfinite(min_rsi) else np.nan
        gap_keep=min_gap/high_gap if np.isfinite(min_gap) and np.isfinite(high_gap) and high_gap!=0 else np.nan
        rsi_keep=min_rsi/high_rsi if np.isfinite(min_rsi) and np.isfinite(high_rsi) and high_rsi!=0 else np.nan
        rows.append(dict(symbol=sym,time=t1,first_rebound_high_time=t0,pullback_start=r.pullback_start,
            price=f(r.price),base_low=f(r.base_low),structural_stop=f(r.structural_stop),
            total_rebound_pct=(f(r.price)/f(r.base_low)-1)*100,
            pullback_minutes=(t1-t0).total_seconds()/60.0,
            high_gap_delta=high_gap,min_gap_delta=min_gap,entry_gap_delta=entry_gap,gap_drop=gap_drop,gap_keep_ratio=gap_keep,gap_positive_all=bool((gd>0).all()),
            high_rsi_slope=high_rsi,min_rsi_slope=min_rsi,entry_rsi_slope=entry_rsi,rsi_drop=rsi_drop,rsi_keep_ratio=rsi_keep,rsi_positive_all=bool((rs>0).all()),
            price_min=f(px.min()),price_max=f(px.max()),volume_accel=f(r.volume_accel)))
        print(f'\n--- {sym} {t1} ---')
        print(w[['time','px','gap_delta','rsi_slope','mid_slope8','slope_gain3','volume_accel_3v10']].to_string(index=False))

    out=pd.DataFrame(rows).sort_values(['time','symbol'])
    print('\n=== SUMMARY ===')
    print(out.to_string(index=False))
    path=sm.OUT_DIR/'v21_v_rebound_pullback_momentum.csv'
    out.to_csv(path,index=False)
    print(f'\nWROTE {path}')

if __name__=='__main__':main()
