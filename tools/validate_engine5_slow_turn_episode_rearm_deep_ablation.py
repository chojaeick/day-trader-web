from __future__ import annotations

"""Validate Slow-turn re-arm as transition episodes and ablate DEEP exclusion.

No V20 rule is changed. No new strategy is created.
This only asks whether the existing Slow-turn candidate/state handling should:
  1) re-arm after a READY episode ends instead of locking the whole symbol/day, and
  2) keep rejecting all zero_cross_bars > 12 cases, or allow already-tested
     persistence/price-confirmed Slow-turn cases inside the same strategy family.

Episode handling is causal: candidates close in time belong to one READY episode;
within an episode we keep evaluating until one candidate is selected, then suppress
later duplicates in that same episode. A later episode can re-arm.
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
import tools.validate_engine5_slow_turn_provisional_full as prov
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_DETAIL=OUT_DIR/'slow_turn_episode_rearm_deep_detail.csv'
OUT_SUMMARY=OUT_DIR/'slow_turn_episode_rearm_deep_summary.csv'
TARGET_SYM='950160'
TARGET_DAY=pd.Timestamp('2026-08-14').date()
THRESHOLD=50
FEE_RT_PCT=0.25

# Diagnostic sensitivity only. A new episode begins after this many READY-free minutes.
EPISODE_GAPS=(2,3,5)
DEEP_POLICIES=('EXCLUDE','P60_60_PX075','P80_70_PX075','P80_70_PX100')


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')
def finite(x):
    try:
        v=float(x); return v if np.isfinite(v) else np.nan
    except Exception:return np.nan


def deep_ok(r,policy):
    if policy=='EXCLUDE': return False
    p5=finite(r.joint5_persistence); p1=finite(r.joint1_persistence); px=finite(r.price_progress_1m_pct)
    if not (np.isfinite(p5) and np.isfinite(p1) and np.isfinite(px)): return False
    if policy=='P60_60_PX075': return p5>=0.60 and p1>=0.60 and px>=0.75
    if policy=='P80_70_PX075': return p5>=0.80 and p1>=0.70 and px>=0.75
    if policy=='P80_70_PX100': return p5>=0.80 and p1>=0.70 and px>=1.00
    raise ValueError(policy)


def policy_ok(r,policy):
    # Keep all existing NEAR/MID/BOUNDARY rules exactly as-is.
    if str(r.regime)!='DEEP_GT12':
        return bool(r.selected_current), str(r.select_reason)
    ok=deep_ok(r,policy)
    return bool(ok), ('PASS_DEEP_'+policy if ok else 'DEEP_'+policy+'_FAIL')


def assign_episodes(df,gap_min):
    out=df.sort_values(['symbol','day','entry_time','ready_time']).copy()
    ids=[]
    for (_, _),g in out.groupby(['symbol','day'],sort=False):
        last=None; eid=0
        for _,r in g.iterrows():
            t=pd.Timestamp(r.ready_time)
            if last is None or (t-last)>pd.Timedelta(minutes=gap_min): eid+=1
            ids.append((r.name,eid))
            last=t
    mp=dict(ids)
    out['episode_id']=[mp[i] for i in out.index]
    return out


def select_episode_causally(df,policy,gap_min):
    x=assign_episodes(df,gap_min)
    selected=[]; reasons=[]
    for (_,_,_),g in x.groupby(['symbol','day','episode_id'],sort=False):
        chosen=False
        for idx,r in g.sort_values(['entry_time','ready_time']).iterrows():
            ok,reason=policy_ok(r,policy)
            if ok and not chosen:
                selected.append(idx); reasons.append((idx,reason)); chosen=True
    out=x.loc[selected].copy() if selected else x.iloc[0:0].copy()
    rmap=dict(reasons)
    if len(out): out['episode_select_reason']=[rmap[i] for i in out.index]
    return out,x


def stat(trades):
    p=num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net=p-FEE_RT_PCT
    gp=float(net[net>0].sum()) if len(net) else 0.0; gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(trades=len(net),wins=int((net>0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,pf=(gp/gl if gl>0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan)


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

    rows=[]; detail=[]
    for gap in EPISODE_GAPS:
        for policy in DEEP_POLICIES:
            sel,tagged=select_episode_causally(allc,policy,gap)
            sev=zd.event_stream(sel)
            trades=multi.simulate_multi(packed,sev,states,THRESHOLD)
            ss=stat(trades)
            deep_sel=int((sel.regime=='DEEP_GT12').sum()) if len(sel) else 0
            later_sel=int((sel.candidate_no>1).sum()) if len(sel) else 0
            target=sel[(sel.symbol==TARGET_SYM)&(sel.day==TARGET_DAY)&(pd.to_datetime(sel.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))]
            target_time=str(pd.Timestamp(target.iloc[0].entry_time)) if len(target) else ''
            rows.append(dict(episode_gap_min=gap,deep_policy=policy,signals=len(sel),later_candidate_signals=later_sel,
                             deep_signals=deep_sel,target_selected=bool(len(target)),target_entry_time=target_time,**ss))
            if len(sel):
                q=sel.drop(columns=['event'],errors='ignore').copy(); q['episode_gap_min']=gap; q['deep_policy']=policy
                detail.append(q)

    summary=pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY,index=False)
    if detail: pd.concat(detail,ignore_index=True).to_csv(OUT_DETAIL,index=False)

    print('\n=== SLOW-TURN EPISODE RE-ARM + DEEP ABLATION ===')
    print('No V20 rule changed. Existing NEAR/MID/BOUNDARY selector unchanged.')
    print(f'All READY+1m confirmed candidates={len(allc)}')
    print('Episode rule: keep evaluating within one READY episode until one signal passes; then lock only that episode.')
    print('DEEP policies are previously tested persistence/price diagnostics, not new production thresholds.')
    show=['episode_gap_min','deep_policy','signals','later_candidate_signals','deep_signals','target_selected','target_entry_time','trades','wins','win_pct','net_sum_pct','pf','max_loss_pct']
    print('\n=== SUMMARY ===')
    print(summary[show].to_string(index=False))

    print('\n=== TARGET 950160 2026-08-14 10:30~11:20, GAP=3 ===')
    target_all=assign_episodes(allc,3)
    target_all=target_all[(target_all.symbol==TARGET_SYM)&(target_all.day==TARGET_DAY)&(pd.to_datetime(target_all.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))].copy()
    if target_all.empty: print('NONE')
    else:
        cols=['candidate_no','episode_id','ready_time','entry_time','entry_price','zero_cross_bars','joint5_persistence','joint1_persistence','price_progress_1m_pct','regime','selected_current','select_reason']
        print(target_all[cols].to_string(index=False))
        for policy in DEEP_POLICIES:
            sel,_=select_episode_causally(allc,policy,3)
            q=sel[(sel.symbol==TARGET_SYM)&(sel.day==TARGET_DAY)&(pd.to_datetime(sel.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))]
            if len(q): print(f'  {policy}: SELECT {pd.Timestamp(q.iloc[0].entry_time)} candidate_no={int(q.iloc[0].candidate_no)} episode={int(q.iloc[0].episode_id)}')
            else: print(f'  {policy}: NO TARGET SIGNAL')

    print('\nREADING:')
    print('- Stable results across gap 2/3/5 mean the episode concept is not dependent on one arbitrary reset gap.')
    print('- If target appears only when DEEP exclusion is relaxed, zero_cross_bars>12 is the second blocker.')
    print('- Compare added/deep signal count and realized trade quality before changing the actual Slow-turn state machine.')
    print('WROTE',OUT_SUMMARY)
    print('WROTE',OUT_DETAIL)

if __name__=='__main__': main()
