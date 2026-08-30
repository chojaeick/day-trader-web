from __future__ import annotations

from dataclasses import replace
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
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD=50
RAW_MIN=30.0
LEG_MIN=2.0
CONFIGS=[
    ('STOP1.5_VOL1.5',1.5,1.5),
    ('STOP2.0_VOL1.5',2.0,1.5),
]
TARGETS=[('950160','2026-08-14'),('950260','2026-08-19')]


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


def add_pullback_reaccel(cand, feature_by_symbol):
    out=cand.copy()
    out['pullback_gap_delta']=np.nan
    out['pullback_rsi_slope']=np.nan
    out['macd_reaccel']=False
    out['rsi_reaccel']=False
    out['reaccel_pass']=False
    for idx,r in out.iterrows():
        sym=n(r.symbol); z=feature_by_symbol.get(sym)
        if z is None or len(z)==0: continue
        ts=pd.Timestamp(r.pullback_start)
        q=z[pd.to_datetime(z.time)==ts]
        if q.empty: continue
        p=q.iloc[-1]
        pg=f(p.gap_delta); pr=f(p.rsi_slope)
        cg=f(r.gap_delta); cr=f(r.rsi_slope)
        out.at[idx,'pullback_gap_delta']=pg
        out.at[idx,'pullback_rsi_slope']=pr
        mg=np.isfinite(cg) and np.isfinite(pg) and cg>pg
        rr=np.isfinite(cr) and np.isfinite(pr) and cr>pr
        out.at[idx,'macd_reaccel']=bool(mg)
        out.at[idx,'rsi_reaccel']=bool(rr)
        out.at[idx,'reaccel_pass']=bool(mg and rr)
    return out


def main():
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    strength={s:ms.add_strength(x) for s,x in scored.items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)

    micros={}; allc=[]; feature_by_symbol={}
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True)
        pf,m=old.load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m
        z=sm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        feature_by_symbol[sym]=z
        c=sm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): allc.append(c)

    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    base_tr=multi.simulate_multi(packed,ev20,states,THRESHOLD)
    print('\n=== BASE V20 ===')
    print(pd.DataFrame([sm.stat('V20',base_tr)]).to_string(index=False))

    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO V CANDIDATES'); return
    cand=add_pullback_reaccel(cand,feature_by_symbol)
    cand.drop(columns=['event']).to_csv(sm.OUT_DIR/'v21_v_rebound_reaccel_candidates.csv',index=False)

    print('\n=== REACCEL FILTER ===')
    print('PASS only when entry MACD gap_delta > pullback-start gap_delta AND entry RSI slope > pullback-start RSI slope.')
    rows=[]
    for label,cap,vol in CONFIGS:
        for use_filter in [False,True]:
            q0=cand[cand.reaccel_pass].copy() if use_filter else cand.copy()
            vev,meta,q=sm.select(q0,RAW_MIN,LEG_MIN,cap,vol)
            extra=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
            merged=old.simulate_with_v_stop(packed,sm.merge(ev20,vev),states,THRESHOLD,meta)
            se=sm.stat('EXTRA',extra); sx=sm.stat('MERGED',merged)
            row=dict(config=label,reaccel='ON' if use_filter else 'OFF',signals=len(q),**sx,
                     extra_trades=se['trades'],extra_wins=se['wins'],extra_win_pct=se['win_pct'],
                     extra_net=se['net_sum_pct'],extra_pf=se['pf'],extra_max_loss=se['max_loss_pct'])
            rows.append(row)
    summary=pd.DataFrame(rows)
    print(summary.to_string(index=False))
    summary.to_csv(sm.OUT_DIR/'v21_v_rebound_reaccel_summary.csv',index=False)

    print('\n=== STOP<=2.0 / VOL>=1.5 SIGNALS WITH PASS/FAIL ===')
    _,_,q2=sm.select(cand,RAW_MIN,LEG_MIN,2.0,1.5)
    cols=['symbol','time','price','structural_stop','stop_dist_pct','volume_accel','pullback_start','gap_delta','pullback_gap_delta','rsi_slope','pullback_rsi_slope','macd_reaccel','rsi_reaccel','reaccel_pass']
    print(q2[cols].sort_values(['time','symbol']).to_string(index=False) if len(q2) else 'NONE')

    print('\n=== TARGETS ===')
    for sym,date in TARGETS:
        q=cand[(cand.symbol==sym)&(pd.to_datetime(cand.time).dt.strftime('%Y-%m-%d')==date)]
        print(f'\n{sym} {date}')
        print(q[cols].sort_values('time').to_string(index=False) if len(q) else 'NONE')

    print('\nWROTE v21_v_rebound_reaccel_candidates.csv / summary.csv')

if __name__=='__main__':main()
