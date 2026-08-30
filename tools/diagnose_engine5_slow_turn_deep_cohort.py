from __future__ import annotations

"""Winner/loss diagnosis for the strong DEEP Slow-turn cohort.

This does not tune or change any strategy rule. It freezes the previously tested
P80_70_PX100 diagnostic cohort at episode_gap=3 and compares structural features
between realized winners and losers. Detailed rows go to CSV; terminal output is
kept intentionally concise.
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
OUT_CASES=OUT_DIR/'slow_turn_deep_p80_70_px100_cases.csv'
OUT_COMPARE=OUT_DIR/'slow_turn_deep_p80_70_px100_win_loss_compare.csv'
POLICY='P80_70_PX100'
EPISODE_GAP=3
THRESHOLD=50
FEE_RT_PCT=0.25
TARGET_SYM='950160'
TARGET_DAY=pd.Timestamp('2026-08-14').date()


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')

def norm_times(df):
    out=df.copy()
    for c in ['entry_time','exit_time']:
        if c in out: out[c]=pd.to_datetime(out[c])
    if 'symbol' in out: out['symbol']=out['symbol'].astype(str).str.zfill(6)
    return out


def metric_summary(cases,metric):
    w=num(cases.loc[cases.outcome=='WIN',metric]).dropna()
    l=num(cases.loc[cases.outcome=='LOSS',metric]).dropna()
    wm=float(w.median()) if len(w) else np.nan
    lm=float(l.median()) if len(l) else np.nan
    return dict(metric=metric,wins_n=len(w),losses_n=len(l),win_median=wm,loss_median=lm,median_diff=wm-lm if np.isfinite(wm) and np.isfinite(lm) else np.nan,
                win_mean=float(w.mean()) if len(w) else np.nan,loss_mean=float(l.mean()) if len(l) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
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
    deep=selected[selected.regime.eq('DEEP_GT12')].copy()
    sev=zd.event_stream(selected)
    trades=norm_times(multi.simulate_multi(packed,sev,states,THRESHOLD))
    deep['symbol']=deep.symbol.astype(str).str.zfill(6)
    deep['entry_time']=pd.to_datetime(deep.entry_time)

    # Match realized outcomes causally by the actual selected symbol/entry timestamp.
    keep=['symbol','entry_time','exit_time','pnl_pct']
    missing=[c for c in keep if c not in trades.columns]
    if missing:
        raise KeyError(f'simulate_multi output missing columns: {missing}; got={list(trades.columns)}')
    cases=deep.merge(trades[keep],on=['symbol','entry_time'],how='left',validate='one_to_one')
    cases['net_pct']=num(cases.pnl_pct)-FEE_RT_PCT
    cases['outcome']=np.where(cases.net_pct>0,'WIN','LOSS')

    metrics=[
        'mid_slope8','slope_gain','recovery_per_bar','zero_cross_bars',
        'gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence',
        'price_progress_1m_pct','close_progress_6m_pct','rise_from_6m_low_pct',
        'entry_vs_6m_high_pct','last1m_return_pct','last2m_return_pct'
    ]
    metrics=[m for m in metrics if m in cases.columns]
    comp=pd.DataFrame([metric_summary(cases,m) for m in metrics])
    # Rank descriptively by standardized median separation, not as a threshold optimizer.
    scales=[]
    for m in metrics:
        s=num(cases[m]).dropna(); iqr=float(s.quantile(.75)-s.quantile(.25)) if len(s) else np.nan
        scales.append(iqr if np.isfinite(iqr) and iqr>0 else np.nan)
    comp['iqr']=scales
    comp['median_diff_iqr']=comp.median_diff/comp.iqr
    comp['abs_sep']=comp.median_diff_iqr.abs()
    comp=comp.sort_values('abs_sep',ascending=False).reset_index(drop=True)

    cases.drop(columns=['event'],errors='ignore').to_csv(OUT_CASES,index=False)
    comp.to_csv(OUT_COMPARE,index=False)

    net=num(cases.net_pct)
    wins=int((net>0).sum()); losses=int((net<=0).sum())
    print('\n=== SLOW-TURN DEEP STRONG COHORT: WIN vs LOSS ===')
    print(f'Policy={POLICY} episode_gap={EPISODE_GAP}m | deep signals={len(cases)} wins={wins} losses={losses} net={net.sum():+.3f}%')
    print('No threshold changed. Rankings below are descriptive separation only.')
    print('\n=== TOP STRUCTURAL SEPARATIONS (median) ===')
    show=comp.head(8)[['metric','win_median','loss_median','median_diff','median_diff_iqr']]
    print(show.to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    tgt=cases[(cases.symbol==TARGET_SYM)&(cases.day==TARGET_DAY)&(pd.to_datetime(cases.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))]
    print('\n=== TARGET 950160 2026-08-14 ===')
    if tgt.empty:
        print('TARGET NOT IN COHORT')
    else:
        cols=['entry_time','entry_price','outcome','net_pct','mid_slope8','slope_gain','zero_cross_bars','gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence','price_progress_1m_pct','close_progress_6m_pct']
        cols=[c for c in cols if c in tgt.columns]
        print(tgt[cols].to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    print('\nREADING:')
    print('- Large separation that agrees across several related structural metrics is worth a focused causal ablation.')
    print('- Do not choose a cutoff from this table alone; n is small and this is still in-sample diagnosis.')
    print('WROTE',OUT_CASES)
    print('WROTE',OUT_COMPARE)

if __name__=='__main__': main()
