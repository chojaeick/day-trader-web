from __future__ import annotations

from dataclasses import replace
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

THRESHOLD=50
RAW_MIN=30.0
LEG_MIN=2.0
STOP_CAP=2.0
VOL_MIN=1.0
GAP_KEEP=0.9
FEE_RT_PCT=.25


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


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
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True)
        pf,m=old.load_cache(sym,bars,cfg,completed[sym])
        z=sm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        features[sym]=z
        c=sm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): allc.append(c)

    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty: print('NO CANDIDATES'); return
    cand=ra.add_pullback_reaccel(cand,features)
    cand=mp.add_preservation(cand,features)
    q=cand[(cand.stop_dist_pct<=STOP_CAP)&cand.reaccel_pass&(pd.to_numeric(cand.volume_accel,errors='coerce')>=VOL_MIN)&cand.rsi_positive_all&(pd.to_numeric(cand.gap_keep_ratio,errors='coerce')>=GAP_KEEP)].copy()
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first').reset_index(drop=True)
    vev,meta,qsel=sm.select(q,RAW_MIN,LEG_MIN,STOP_CAP,None)
    tr=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)

    print('\n=== FOCUSED V REBOUND SELECTED-3 ===')
    print('Config: RAW30 LEG2.0 STOP<=2.0 VOL>=1.0 REACCEL=ON RSI_POS_ALL GAP_KEEP>=0.9')
    print('\n=== SELECTED CANDIDATES ===')
    cols=['symbol','time','price','base_low','first_rebound_high','first_rebound_high_time','pullback_start','structural_stop','stop_dist_pct','volume_accel','pullback_minutes','gap_keep_ratio','rsi_keep_ratio']
    print(qsel[cols].to_string(index=False) if len(qsel) else 'NONE')

    print('\n=== TRADE OUTCOMES ===')
    if len(tr):
        x=tr.copy()
        x['net_after_fee_pct']=pd.to_numeric(x.pnl_pct,errors='coerce')-FEE_RT_PCT
        show=[c for c in ['symbol','entry_time','entry_price','exit_time','exit_price','pnl_pct','net_after_fee_pct','exit_reason'] if c in x.columns]
        print(x[show].to_string(index=False))
    else: print('NONE')

    print('\n=== ENTRY +/- 5 MINUTE MICRO WINDOWS ===')
    for _,r in qsel.iterrows():
        sym=n(r.symbol); ts=pd.Timestamp(r.time); z=features[sym].copy(); z['time']=pd.to_datetime(z.time)
        w=z[(z.time>=ts-pd.Timedelta(minutes=5))&(z.time<=ts+pd.Timedelta(minutes=5))].copy()
        keep=[c for c in ['time','px','lo','hi','gap_delta','rsi_slope','mid_slope8','slope_gain3','volume_accel_3v10'] if c in w.columns]
        print(f'\n--- {sym} entry={ts} structural_stop={f(r.structural_stop):.4f} ---')
        print(w[keep].to_string(index=False))

    print('\n=== LOSS INTERPRETATION INPUTS ===')
    if len(tr):
        for _,t in tr.iterrows():
            sym=n(t.symbol) if 'symbol' in t else ''
            et=pd.Timestamp(t.entry_time) if 'entry_time' in t else pd.NaT
            c=qsel[(qsel.symbol.astype(str).str.zfill(6)==sym)&(pd.to_datetime(qsel.time)==et)]
            if c.empty: continue
            r=c.iloc[0]
            net=f(t.pnl_pct)-FEE_RT_PCT
            print(f"{sym} {et}: net={net:+.6f}% reason={getattr(t,'exit_reason','')} stop_dist={f(r.stop_dist_pct):.6f}% pullback={f(r.pullback_minutes):.1f}m gap_keep={f(r.gap_keep_ratio):.6f} rsi_keep={f(r.rsi_keep_ratio):.6f} vol={f(r.volume_accel):.6f}")

if __name__=='__main__': main()
