from __future__ import annotations

"""Integrated full-history validation of the revised *existing* Slow-turn structure.

V20 and V-rebound are obtained from validate_engine5_integrated_full_history.build_sources
unchanged. Only its SLOW_TURN tagged signals are replaced.

Slow-turn change under test:
  - re-arm by causal READY episode instead of first candidate per symbol/day;
  - keep existing NEAR/MID/BOUNDARY selection unchanged;
  - allow DEEP only with P80_70_PX100 strong joint persistence/price confirmation;
  - additionally require normalized negative mid_slope8 depth to be sufficiently flat.

The normalized slope cutoff is NOT frozen. Several coarse values are carried through the
full integrated simulator to check whether the previously observed plateau survives source
conflicts and position ownership.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.diagnose_engine5_slow_turn_rearm_ablation as rearm
import tools.validate_engine5_slow_turn_episode_rearm_deep_ablation as ep
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_SUMMARY=OUT_DIR/'integrated_slow_turn_rearm_deep_summary.csv'
OUT_TRADES=OUT_DIR/'integrated_slow_turn_rearm_deep_trades.csv'
OUT_SIGNALS=OUT_DIR/'integrated_slow_turn_rearm_deep_signals.csv'
EPISODE_GAP=3
DEEP_POLICY='P80_70_PX100'
CUTS=(-0.15,-0.20,-0.30,-0.50)
TARGET_SYM='950160'
TARGET_DAY=pd.Timestamp('2026-08-14').date()


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')

def deep_ok(r,cut):
    p5=float(r.joint5_persistence) if pd.notna(r.joint5_persistence) else np.nan
    p1=float(r.joint1_persistence) if pd.notna(r.joint1_persistence) else np.nan
    px=float(r.price_progress_1m_pct) if pd.notna(r.price_progress_1m_pct) else np.nan
    ns=float(r.norm_mid_slope_pct) if pd.notna(r.norm_mid_slope_pct) else np.nan
    return bool(np.isfinite(p5) and p5>=0.80 and np.isfinite(p1) and p1>=0.70 and
                np.isfinite(px) and px>=1.00 and np.isfinite(ns) and ns>=float(cut))


def select_revised(allc,cut):
    x=ep.assign_episodes(allc,EPISODE_GAP)
    chosen=[]
    for (_,_,_),g in x.groupby(['symbol','day','episode_id'],sort=False):
        locked=False
        for idx,r in g.sort_values(['entry_time','ready_time']).iterrows():
            if locked: break
            if str(r.regime)=='DEEP_GT12':
                ok=deep_ok(r,cut)
            else:
                ok=bool(r.selected_current)
            if ok:
                chosen.append(idx); locked=True
    return x.loc[chosen].copy() if chosen else x.iloc[0:0].copy()


def build_all_slow(raw,cfg,completed,micros):
    parts=[]
    for i,s in enumerate(raw,1):
        print(f'[SLOW {i}/{len(raw)}] {s}',flush=True)
        pf,_=st.load_or_build_cache(s,raw[s],cfg,completed[s])
        q=rearm.build_all_candidates(s,pf,micros[s],completed[s])
        if len(q): parts.append(q)
    if not parts: return pd.DataFrame()
    x=pd.concat(parts,ignore_index=True)
    x['symbol']=x.symbol.astype(str).str.zfill(6)
    x['entry_time']=pd.to_datetime(x.entry_time)
    x['ready_time']=pd.to_datetime(x.ready_time)
    x['day']=x.entry_time.dt.date
    x=x.sort_values(['symbol','day','entry_time','ready_time']).reset_index(drop=True)
    x['candidate_no']=x.groupby(['symbol','day']).cumcount()+1
    x['norm_mid_slope_pct']=num(x.mid_slope8)/num(x.entry_price)*100.0
    return x


def slow_tags(sel):
    sev=zd.event_stream(sel)
    meta={(pd.Timestamp(r.entry_time),n(r.symbol)):{'regime':str(r.regime),
          'norm_mid_slope_pct':float(r.norm_mid_slope_pct)} for _,r in sel.iterrows()}
    out=[]
    for ts,cs in sev.items():
        for c in cs:
            out.append(dict(source='SLOW_TURN',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,
                            meta=meta.get((pd.Timestamp(ts),n(c[0])),{})))
    return out


def source_counts(tagged):
    s=pd.Series([x['source'] for x in tagged],dtype=str)
    return {k:int(v) for k,v in s.value_counts().items()}


def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:rt.add_completed_strength(f) for s,f in scored.items()}
    completed=strength
    micros={s:h.build_micro(raw[s],cfg) for s in raw}

    print('=== BUILD UNCHANGED V20 + V_REBOUND BASE SOURCES ===',flush=True)
    old_tagged=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    non_slow=[x for x in old_tagged if x['source']!='SLOW_TURN']
    old_slow=[x for x in old_tagged if x['source']=='SLOW_TURN']

    print('=== BUILD RE-ARMED SLOW-TURN CANDIDATES ===',flush=True)
    allc=build_all_slow(raw,cfg,completed,micros)
    if allc.empty: raise SystemExit('NO SLOW CANDIDATES')

    rows=[]; trade_parts=[]; sig_parts=[]

    # Existing integrated result, recomputed with unchanged build_sources.
    old_tr=integ.simulate(packed,states,old_tagged)
    old_stat=integ.stat('OLD_INTEGRATED',old_tr)
    oc=source_counts(old_tagged)
    rows.append(dict(cut='OLD',slow_signals=len(old_slow),target_selected=False,
                     v20_signals=oc.get('V20',0),v_signals=oc.get('V_REBOUND',0),**old_stat))

    target_base=allc[(allc.symbol==TARGET_SYM)&(allc.day==TARGET_DAY)&
                     (allc.entry_time.dt.strftime('%H:%M').between('10:30','11:20'))]

    for cut in CUTS:
        sel=select_revised(allc,cut)
        stags=slow_tags(sel)
        tagged=non_slow+stags
        tagged=sorted(tagged,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source']))
        tr=integ.simulate(packed,states,tagged)
        ss=integ.stat(f'REVISED_{cut}',tr)
        cnt=source_counts(tagged)
        tgt=sel[(sel.symbol==TARGET_SYM)&(sel.day==TARGET_DAY)&
                (sel.entry_time.dt.strftime('%H:%M').between('10:30','11:20'))]
        rows.append(dict(cut=cut,slow_signals=len(sel),target_selected=bool(len(tgt)),
                         target_entry=(str(pd.Timestamp(tgt.iloc[0].entry_time)) if len(tgt) else ''),
                         v20_signals=cnt.get('V20',0),v_signals=cnt.get('V_REBOUND',0),**ss))
        q=tr.copy(); q['cut']=cut; trade_parts.append(q)
        q2=sel.drop(columns=['event'],errors='ignore').copy(); q2['cut']=cut; sig_parts.append(q2)

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY,index=False)
    if trade_parts: pd.concat(trade_parts,ignore_index=True).to_csv(OUT_TRADES,index=False)
    if sig_parts: pd.concat(sig_parts,ignore_index=True).to_csv(OUT_SIGNALS,index=False)

    print('\n=== INTEGRATED SLOW-TURN RE-ARM + DEEP NORMALIZED SLOPE ===')
    print('V20/V_REBOUND source construction and exit ownership are unchanged from integrated_full_history.')
    print(f'Old slow signals={len(old_slow)} | all re-armed READY+1m candidates={len(allc)}')
    show=['cut','slow_signals','target_selected','target_entry','v20_signals','v_signals','trades','wins','win_pct','net_sum_pct','pf','max_loss_pct']
    show=[c for c in show if c in summary.columns]
    print('\n=== SUMMARY ===')
    print(summary[show].to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    print('\n=== TARGET CANDIDATES 950160 2026-08-14 10:30~11:20 ===')
    if target_base.empty: print('NONE')
    else:
        cols=['candidate_no','ready_time','entry_time','entry_price','regime','joint5_persistence','joint1_persistence','price_progress_1m_pct','norm_mid_slope_pct']
        print(target_base[cols].to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    print('\nREADING:')
    print('- OLD must reproduce the prior integrated behavior before interpreting revised rows.')
    print('- V20 and V_REBOUND signal counts should stay invariant across revised cut rows.')
    print('- Prefer a broad revised-cut plateau that keeps the 950160 10:59 target; do not freeze the best in-sample point.')
    print('WROTE',OUT_SUMMARY)
    print('WROTE',OUT_TRADES)
    print('WROTE',OUT_SIGNALS)

if __name__=='__main__': main()
