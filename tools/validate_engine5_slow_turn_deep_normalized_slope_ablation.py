from __future__ import annotations

"""Focused ablation of normalized slope depth inside strong DEEP Slow-turn.

No V20 rule changes. Existing Slow-turn episode re-arm and P80_70_PX100 strong
DEEP cohort are held fixed. This script tests only whether the absolute negative
5m mid_slope8 should be interpreted relative to entry price, so different price
levels are comparable.

This is diagnostic/in-sample. Cutoffs are a coarse sensitivity surface, not a
production threshold search.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.diagnose_engine5_slow_turn_rearm_ablation as rearm
import tools.validate_engine5_slow_turn_episode_rearm_deep_ablation as ep
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_SUMMARY=OUT_DIR/'slow_turn_deep_normalized_slope_ablation_summary.csv'
OUT_CASES=OUT_DIR/'slow_turn_deep_normalized_slope_ablation_cases.csv'
POLICY='P80_70_PX100'
EPISODE_GAP=3
THRESHOLD=50
FEE_RT_PCT=0.25
TARGET_SYM='950160'
TARGET_DAY=pd.Timestamp('2026-08-14').date()
# Negative mid_slope8 expressed as percent of entry price. Less negative = flatter.
# Coarse sensitivity only; BASE means no slope-depth guard.
CUTS=(None,-0.10,-0.15,-0.20,-0.30,-0.50,-0.75,-1.00)


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')

def stat(trades):
    p=num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net=p-FEE_RT_PCT
    gp=float(net[net>0].sum()) if len(net) else 0.0
    gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(trades=len(net),wins=int((net>0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,pf=(gp/gl if gl>0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(raw[s],cfg) for s in raw}

    parts=[]
    for i,s in enumerate(raw,1):
        print(f'[{i}/{len(raw)}] {s}',flush=True)
        pf,_=st.load_or_build_cache(s,raw[s],cfg,completed[s])
        q=rearm.build_all_candidates(s,pf,micros[s],completed[s])
        if len(q): parts.append(q)
    allc=pd.concat(parts,ignore_index=True)
    allc['day']=pd.to_datetime(allc.entry_time).dt.date
    allc=allc.sort_values(['symbol','day','entry_time','ready_time']).reset_index(drop=True)
    allc['candidate_no']=allc.groupby(['symbol','day']).cumcount()+1

    selected,_=ep.select_episode_causally(allc,POLICY,EPISODE_GAP)
    selected=selected.copy()
    selected['norm_mid_slope_pct']=num(selected.mid_slope8)/num(selected.entry_price)*100.0
    selected['is_deep']=selected.regime.eq('DEEP_GT12')

    rows=[]; details=[]
    for cut in CUTS:
        q=selected.copy()
        if cut is not None:
            # Guard applies only to DEEP branch. Existing NEAR/MID/BOUNDARY are untouched.
            keep=(~q.is_deep) | (num(q.norm_mid_slope_pct)>=cut)
            q=q[keep].copy()
        sev=zd.event_stream(q)
        trades=multi.simulate_multi(packed,sev,states,THRESHOLD)
        ss=stat(trades)
        deep_n=int(q.is_deep.sum())
        tgt=q[(q.symbol==TARGET_SYM)&(q.day==TARGET_DAY)&(pd.to_datetime(q.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))]
        rows.append(dict(cut='BASE' if cut is None else cut,signals=len(q),deep_signals=deep_n,
                         target_selected=bool(len(tgt)),target_norm_slope_pct=(float(tgt.iloc[0].norm_mid_slope_pct) if len(tgt) else np.nan),**ss))
        z=q.drop(columns=['event'],errors='ignore').copy(); z['cut']='BASE' if cut is None else cut
        details.append(z)

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY,index=False)
    pd.concat(details,ignore_index=True).to_csv(OUT_CASES,index=False)

    # Descriptive normalized-slope distribution for the realized DEEP baseline cohort.
    deep=selected[selected.is_deep].copy()
    sev=zd.event_stream(selected)
    tr=multi.simulate_multi(packed,sev,states,THRESHOLD).copy()
    tr['symbol']=tr.symbol.astype(str).str.zfill(6); tr['entry_time']=pd.to_datetime(tr.entry_time)
    deep['symbol']=deep.symbol.astype(str).str.zfill(6); deep['entry_time']=pd.to_datetime(deep.entry_time)
    m=deep.merge(tr[['symbol','entry_time','pnl_pct']],on=['symbol','entry_time'],how='left')
    m['net_pct']=num(m.pnl_pct)-FEE_RT_PCT; m['outcome']=np.where(m.net_pct>0,'WIN','LOSS')

    print('\n=== SLOW-TURN DEEP NORMALIZED SLOPE ABLATION ===')
    print('Only DEEP normalized slope depth changes. P80_70_PX100 + episode re-arm are fixed.')
    for outcome in ['WIN','LOSS']:
        x=num(m.loc[m.outcome==outcome,'norm_mid_slope_pct']).dropna()
        if len(x): print(f'{outcome}: n={len(x)} median={x.median():+.4f}% q25={x.quantile(.25):+.4f}% q75={x.quantile(.75):+.4f}%')
    print('\n=== COARSE SENSITIVITY ===')
    print(summary[['cut','signals','deep_signals','target_selected','target_norm_slope_pct','trades','wins','win_pct','net_sum_pct','pf','max_loss_pct']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    tgt=m[(m.symbol==TARGET_SYM)&(m.day==TARGET_DAY)&(pd.to_datetime(m.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))]
    print('\n=== TARGET 950160 2026-08-14 ===')
    if len(tgt):
        print(tgt[['entry_time','entry_price','mid_slope8','norm_mid_slope_pct','net_pct','outcome']].to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    else: print('TARGET NOT IN BASE DEEP COHORT')
    print('\nREADING: prefer a broad plateau that keeps the target and improves cohort quality; do not freeze an exact cutoff from this in-sample surface.')
    print('WROTE',OUT_SUMMARY)
    print('WROTE',OUT_CASES)

if __name__=='__main__': main()
