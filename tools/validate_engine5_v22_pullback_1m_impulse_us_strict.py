from __future__ import annotations

"""Strict-causal US validation for V22 pullback re-entry.

Fixes the timing bug in validate_engine5_v22_pullback_1m_impulse_us.py:
raw 1m bars are open-time stamped, while provisional_5m() excludes rows with
time >= probe_ts.  The old script evaluated MACD/RSI/trend at `ts` but tested
impulse/green using the bar stamped `ts`, so the indicator state did NOT include
the very completed 1m impulse bar being used as the trigger.

Here, for a bar stamped `bar_ts`:
  - the completed-bar decision time is bar_ts + 1 minute
  - provisional state is computed at decision_time, therefore includes bar_ts
  - entry is placed at the next 1m bar open (decision_time), avoiding lookahead

Baseline remains finalized US V22: corrected55 + R3_B05 + remaining JUMP>=15 veto.
No pullback-specific 5m stop ratchet.
"""

import pickle
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_v22_late_score_spike_veto_us as usv
import tools.validate_engine5_v22_r3b05_plus_jump_veto_us as finalus
import tools.validate_engine5_v22_uptrend_pullback_reentry as pb

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_pullback_1m_impulse_us_strict')
IMPULSE_PCTS = [0.5, 0.7, 1.0]
FEE = 0.25
JUMP_VETO = 15.0
MIN_SCORE = 65.0
GROUPS = {
    'VETO15_ONLY': ['VETO15'],
    'LOSING_EXIT_ONLY': ['LOSING_EXIT'],
    'BOTH': ['VETO15', 'LOSING_EXIT'],
}


def n(x): return str(x).zfill(6)

def finite(x):
    try:
        z=float(x); return z if np.isfinite(z) else np.nan
    except Exception:
        return np.nan


def metrics(label,trades):
    gross=pd.to_numeric(trades.get('pnl_pct'),errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    net=gross-FEE; gp=float(net[net>0].sum()) if len(net) else 0.; gl=float(-net[net<0].sum()) if len(net) else 0.
    return dict(case=label,trades=len(net),wins=int((net>0).sum()),win_pct=float((net>0).mean()*100.) if len(net) else 0.,
                net_sum_pct=float(net.sum()) if len(net) else 0.,avg_net_pct=float(net.mean()) if len(net) else 0.,
                pf=(gp/gl if gl>0 else np.inf),max_loss_pct=float(net.min()) if len(net) else np.nan,
                max_win_pct=float(net.max()) if len(net) else np.nan,net_loss_ge_3_count=int((net<=-3.).sum()) if len(net) else 0)


def finalized_v22_tags(raw,cfg,completed,micros,old_tags):
    corrected=usv.build_corrected_55_tags(raw,cfg,completed,micros,old_tags)
    score_cache={}; early_tags,changes=finalus.build_r3_b05(corrected,raw,cfg,score_cache)
    early_keys=set((n(r.symbol),pd.Timestamp(r.early_time),str(r.source)) for r in changes.itertuples(index=False)) if len(changes) else set()
    kept=[]; vetoed=[]
    for item in early_tags:
        sym=n(item['symbol']); ts=pd.Timestamp(item['time']); src=str(item['source'])
        if (sym,ts,src) in early_keys:
            kept.append(item); continue
        s0=finalus.score_at_cached(score_cache,raw,sym,ts,cfg)
        s1=finalus.score_at_cached(score_cache,raw,sym,ts-pd.Timedelta(minutes=1),cfg)
        jump=s0-s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
        if np.isfinite(jump) and jump>=JUMP_VETO:
            vetoed.append(dict(symbol=sym,arm_time=ts,arm_reason='VETO15',primary_source=src,primary_time=ts,primary_jump=float(jump)))
        else: kept.append(item)
    return sorted(kept,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source'])),pd.DataFrame(vetoed),changes


def build_arms(final_trades,veto_arms):
    rows=[]
    if len(veto_arms): rows.extend(veto_arms.to_dict('records'))
    for tr in final_trades.itertuples(index=False):
        if float(tr.pnl_pct)-FEE<=0.:
            rows.append(dict(symbol=n(tr.symbol),arm_time=pd.Timestamp(tr.exit_time),arm_reason='LOSING_EXIT',primary_source=str(tr.source),primary_time=pd.Timestamp(tr.entry_time),primary_jump=np.nan))
    a=pd.DataFrame(rows)
    if a.empty:return a
    return a.sort_values(['symbol','arm_time','arm_reason']).drop_duplicates(['symbol','arm_time','arm_reason']).reset_index(drop=True)


def find_candidates_strict(raw,cfg,arms,min_impulse_pct):
    rows=[]
    for arm in arms.itertuples(index=False):
        sym=n(arm.symbol); bars=raw[sym].copy().sort_values('time').reset_index(drop=True); bars['time']=pd.to_datetime(bars.time)
        at=pd.Timestamp(arm.arm_time)
        pre=pb.raw_window(bars,at,10)
        if pre.empty: continue
        pre_low=float(pd.to_numeric(pre.low,errors='coerce').min())
        watch=bars[(bars.time>=at)&(bars.time<=at+pd.Timedelta(minutes=pb.MAX_WATCH_MIN))].copy().reset_index(drop=True)
        if len(watch)<4: continue
        pullback_seen=False; pullback_low=np.inf; down_bars=0
        for i in range(1,len(watch)):
            r=watch.iloc[i]; prev=watch.iloc[i-1]; bar_ts=pd.Timestamp(r.time)
            if (bar_ts-at).total_seconds()/60.<pb.MIN_WAIT_MIN: continue
            if float(r.close)<=float(prev.close):
                pullback_seen=True; down_bars+=1; pullback_low=min(pullback_low,float(r.low))
            elif pullback_seen:
                pullback_low=min(pullback_low,float(r.low))
            if not pullback_seen: continue

            # STRICT FIX: state includes the completed bar r.
            decision_ts=bar_ts+pd.Timedelta(minutes=1)
            st=pb.provisional_state(bars,decision_ts,cfg)
            if st is None: continue
            trend_alive=bool(st['trend_up']) and np.isfinite(st['mid_slope8']) and st['mid_slope8']>0.
            if not trend_alive: break
            higher_low=np.isfinite(pullback_low) and pullback_low>pre_low
            macd_rising=np.isfinite(st['macd_slope']) and st['macd_slope']>0.
            rsi_rising=np.isfinite(st['rsi_slope']) and st['rsi_slope']>0.
            prev_close=float(prev.close)
            impulse=((float(r.close)/prev_close)-1.)*100. if prev_close>0 else np.nan
            green=float(r.close)>float(r.open); impulse_ok=np.isfinite(impulse) and impulse>=float(min_impulse_pct)
            close_above_mid=np.isfinite(st['mid']) and float(r.close)>=st['mid']
            vol_rec=finite(r.volume)>finite(prev.volume)
            score,_,parts=pb.score_candidate(st,higher_low,bool(green and impulse_ok),close_above_mid,vol_rec)
            mandatory=trend_alive and higher_low and macd_rising and rsi_rising and green and impulse_ok and score>=MIN_SCORE
            if not mandatory: continue

            # Enter on the next 1m bar open at decision_ts; do not use completed bar close as a future fill.
            nx=bars[bars.time==decision_ts]
            if nx.empty: continue
            entry_price=float(nx.iloc[0].open)
            rows.append(dict(symbol=sym,arm_time=at,arm_reason=str(arm.arm_reason),primary_source=str(arm.primary_source),
                             primary_time=pd.Timestamp(arm.primary_time),primary_jump=finite(arm.primary_jump),
                             impulse_bar_time=bar_ts,candidate_time=decision_ts,candidate_price=entry_price,
                             trigger_close=float(r.close),pullback_score=float(score),mandatory_pass=True,
                             impulse_threshold_pct=float(min_impulse_pct),impulse_pct=float(impulse),green_1m=bool(green),down_bars=int(down_bars),
                             pre_structural_low=pre_low,pullback_low=float(pullback_low),higher_low=bool(higher_low),reaccel=True,
                             macd=st['macd'],macd_signal=st['macd_signal'],macd_slope=st['macd_slope'],rsi=st['rsi'],rsi_slope=st['rsi_slope'],
                             trend_up=st['trend_up'],mid=st['mid'],mid_slope8=st['mid_slope8'],inner_upper=st['inner_upper'],inner_lower=st['inner_lower'],
                             outer_upper=st['outer_upper'],outer_expanding=st['outer_expanding'],close_above_mid=close_above_mid,volume_recovery=vol_rec,
                             base_live_score=st['entry_score'],**parts))
            break
    return pd.DataFrame(rows)


def make_extra_tags(q):
    tags=[]
    for r in q.itertuples(index=False):
        ev=pb.event_from_candidate(r)
        if ev is None:continue
        tags.append(dict(source='UPTREND_PULLBACK_1M_IMPULSE_STRICT',symbol=n(r.symbol),time=pd.Timestamp(r.candidate_time),event=ev,
                         meta={'arm_reason':str(r.arm_reason),'arm_time':pd.Timestamp(r.arm_time),'impulse_bar_time':pd.Timestamp(r.impulse_bar_time),
                               'impulse_pct':float(r.impulse_pct),'impulse_threshold_pct':float(r.impulse_threshold_pct)}))
    return tags


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    usv.e.apply_us_session_clock()
    with MAP.open('rb') as fh:m=pickle.load(fh)
    raw={n(k):v for k,v in m['raw'].items()}; cfg=m['cfg']; completed={n(k):v for k,v in m['completed'].items()}; micros={n(k):v for k,v in m['micros'].items()}; old_tags=list(m['tags'])
    packed=v8.base.pack_exit_events(raw,cfg); states=base.pack_state_events(base.build_cfg_frames(raw,cfg))
    final_tags,veto_arms,changes=finalized_v22_tags(raw,cfg,completed,micros,old_tags)
    baseline=integ.simulate(packed,states,final_tags); bm=metrics('V22_FINAL_US_BASELINE',baseline)
    print('=== V22 US PULLBACK STRICT-CAUSAL FIX ===',flush=True)
    print('FIX: trigger bar is included in provisional state; entry at next 1m open.',flush=True)
    print('BASELINE',bm,flush=True)
    guard=bm['trades']==104 and bm['wins']==45 and abs(bm['net_sum_pct']-4.079145491731658)<0.001
    print('BASELINE REPRO:', 'PASS' if guard else 'FAIL',flush=True)
    if not guard: raise SystemExit('baseline mismatch')
    arms_all=build_arms(baseline,veto_arms); print('ALL ARMS',len(arms_all),arms_all.arm_reason.value_counts().to_dict(),flush=True)
    summaries=[bm]; cand_parts=[]
    for gname,reasons in GROUPS.items():
        arms=arms_all[arms_all.arm_reason.isin(reasons)].reset_index(drop=True); print('\n===',gname,'arms=',len(arms),'===',flush=True)
        for th in IMPULSE_PCTS:
            q=find_candidates_strict(raw,cfg,arms,th)
            if len(q): q=q.sort_values(['candidate_time','impulse_pct'],ascending=[True,False]).drop_duplicates(['symbol','candidate_time']); qq=q.copy(); qq['group']=gname; cand_parts.append(qq)
            tr=integ.simulate(packed,states,list(final_tags)+make_extra_tags(q)); label=f'{gname}_IMP{str(th).replace(".","p")}_STRICT'
            st=metrics(label,tr); st.update(arms=len(arms),selected_candidates=len(q),impulse_threshold_pct=th)
            ek=set(zip(tr.symbol.astype(str).str.zfill(6),pd.to_datetime(tr.entry_time))) if len(tr) else set()
            st['executed_pullback']=int(sum((n(r.symbol),pd.Timestamp(r.candidate_time)) in ek for r in q.itertuples(index=False))) if len(q) else 0
            summaries.append(st); print(label,st,flush=True)
    sdf=pd.DataFrame(summaries); print('\n=== SUMMARY ==='); print(sdf.to_string(index=False)); sdf.to_csv(OUT/'summary.csv',index=False)
    arms_all.to_csv(OUT/'arms.csv',index=False)
    if cand_parts:
        c=pd.concat(cand_parts,ignore_index=True); c.to_csv(OUT/'candidates.csv',index=False)
        print('\n=== CANDIDATE GATE OUTPUT ==='); print(c[['group','symbol','arm_reason','impulse_bar_time','candidate_time','candidate_price','impulse_pct','pullback_score','macd_slope','rsi_slope']].to_string(index=False))
    print('\nWROTE',OUT,flush=True)

if __name__=='__main__':main()
