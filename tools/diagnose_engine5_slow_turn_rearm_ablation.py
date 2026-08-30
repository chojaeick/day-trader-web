from __future__ import annotations

"""Ablate the existing Slow-turn day-lock without changing strategy thresholds.

Questions:
1) Does the first-candidate-per-symbol/day lock hide later valid Slow-turn READY events?
2) If so, do current NEAR/MID/BOUNDARY/DEEP selection rules still reject them?
3) Specifically, what happens to 950160 on 2026-08-14 around 10:55~11:00?

This is diagnostic only. It does not create a new strategy or modify V20/Slow-turn rules.
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
import tools.validate_engine5_slow_turn_prototype as slow
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.diagnose_engine5_slow_turn_persistence_surface as ps
import tools.validate_engine5_slow_turn_structure_ablation as ab
import tools.validate_engine5_slow_turn_provisional_full as prov
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_DETAIL=OUT_DIR/'slow_turn_rearm_all_candidates.csv'
OUT_SUMMARY=OUT_DIR/'slow_turn_rearm_summary.csv'
TARGET_SYM='950160'
TARGET_DAY=pd.Timestamp('2026-08-14').date()
THRESHOLD=50
FEE_RT_PCT=0.25


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')
def finite(x):
    try:
        v=float(x); return v if np.isfinite(v) else np.nan
    except Exception:return np.nan


def build_all_candidates(sym,pf,micro,completed):
    """Same existing Slow-turn READY + 1m confirmation, but no seen_day lock."""
    z,m=slow.add_slow_turn_features(pf,micro)
    mid=num(z['mid_slope8']); gain=num(z[f'slope_gain_{zd.SLOPE_LB}']); posr=num(z[f'slope_pos_ratio_{zd.SLOPE_LB}'])
    gd=num(z.get('gap_delta')); rs=num(z.get('rsi_slope'))
    minute=z.time.dt.hour*60+z.time.dt.minute
    ready=(
        (minute>=9*60+10)&(minute<base.NO_ENTRY_MINUTE)&(mid<0)&(gain>0)&
        (posr>=zd.SLOPE_RATIO)&(gd>0)&(rs>0)
    )
    comp=completed.copy().sort_values('time'); comp['time']=pd.to_datetime(comp['time'])
    rows=[]
    for _,r in z[ready].iterrows():
        ts=pd.Timestamp(r.time)
        mr=zd.first_micro_confirmation(m,ts)
        if mr is None: continue
        q5=comp[comp.time<=ts.floor('5min')]
        if q5.empty: continue
        ev=zd.event_from_completed(sym,q5.iloc[-1],mr)
        if ev is None: continue
        sg=finite(r[f'slope_gain_{zd.SLOPE_LB}'])
        rec=sg/float(zd.SLOPE_LB) if np.isfinite(sg) else np.nan
        zcb=abs(finite(r.mid_slope8))/rec if np.isfinite(rec) and rec>0 else np.inf
        entry=pd.Timestamp(mr.time)
        q5w=z[(z.time<=ts)&(z.time>=ts-pd.Timedelta(minutes=6))]
        q1w=m[(m.time<=entry)&(m.time>=entry-pd.Timedelta(minutes=6))]
        g5=ps.seq_monotonicity(num(q5w.gap_delta)) if 'gap_delta' in q5w else np.nan
        r5=ps.seq_monotonicity(num(q5w.rsi_slope)) if 'rsi_slope' in q5w else np.nan
        g1=ps.seq_monotonicity(num(q1w.macd_gap_delta_1m)) if 'macd_gap_delta_1m' in q1w else np.nan
        r1=ps.seq_monotonicity(num(q1w.rsi_slope_1m)) if 'rsi_slope_1m' in q1w else np.nan
        ext=ab.metric_window(m,entry)
        row=dict(
            symbol=n(sym),ready_time=ts,entry_time=entry,entry_price=finite(mr.close),
            mid_slope8=finite(r.mid_slope8),slope_gain=sg,recovery_per_bar=rec,zero_cross_bars=zcb,
            gap_delta_5m=finite(r.get('gap_delta')),rsi_slope_5m=finite(r.get('rsi_slope')),
            gap_pos_ratio_1m=finite(mr.gap_pos_ratio_3),rsi_pos_ratio_1m=finite(mr.rsi_pos_ratio_3),
            joint5_persistence=min(g5,r5) if np.isfinite(g5) and np.isfinite(r5) else np.nan,
            joint1_persistence=min(g1,r1) if np.isfinite(g1) and np.isfinite(r1) else np.nan,
            price_progress_1m_pct=ps.price_progress(m,entry),event=ev,
        )
        row.update(ext)
        ok,reg=prov.classify_and_select(pd.Series(row))
        row['regime']=reg; row['selected_current']=bool(ok)
        if reg=='NEAR_LE1_5':
            row['select_reason']='PASS' if ok else 'NEAR price/extension'
        elif reg=='MID_1_5_8':
            row['select_reason']='PASS' if ok else 'MID persistence/price'
        elif reg=='BOUNDARY_8_12':
            row['select_reason']='PASS' if ok else 'BOUNDARY MACD/RSI/price'
        elif reg=='DEEP_GT12':
            row['select_reason']='DEEP excluded'
        else: row['select_reason']='INVALID'
        rows.append(row)
    return pd.DataFrame(rows)


def stat(label,trades):
    p=num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net=p-FEE_RT_PCT
    gp=float(net[net>0].sum()) if len(net) else 0.0; gl=float(-net[net<0].sum()) if len(net) else 0.0
    return dict(label=label,trades=len(net),wins=int((net>0).sum()),win_pct=float((net>0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,pf=(gp/gl if gl>0 else np.inf),max_loss_pct=float(net.min()) if len(net) else np.nan)


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

    all_parts=[]; first_parts=[]
    for i,s in enumerate(raw,1):
        print(f'[{i}/{len(raw)}] {s}',flush=True)
        pf,_=st.load_or_build_cache(s,raw[s],cfg,completed[s])
        a=build_all_candidates(s,pf,micros[s],scored[s])
        if len(a):
            all_parts.append(a)
            x=a.copy(); x['day']=pd.to_datetime(x.entry_time).dt.date
            first_parts.append(x.sort_values('entry_time').groupby(['symbol','day'],as_index=False).head(1).drop(columns='day'))
    allc=pd.concat(all_parts,ignore_index=True) if all_parts else pd.DataFrame()
    first=pd.concat(first_parts,ignore_index=True) if first_parts else pd.DataFrame()
    if allc.empty: raise SystemExit('NO CANDIDATES')
    allc['day']=pd.to_datetime(allc.entry_time).dt.date
    allc['candidate_no']=allc.sort_values('entry_time').groupby(['symbol','day']).cumcount()+1

    # Current selector applied to every re-armed candidate, unchanged.
    selected=allc[allc.selected_current].copy()
    sev=zd.event_stream(selected)
    trades=multi.simulate_multi(packed,sev,states,THRESHOLD)
    s=stat('REARM_CURRENT_SELECTOR',trades)

    target=allc[(allc.symbol==TARGET_SYM)&(allc.day==TARGET_DAY)&(pd.to_datetime(allc.entry_time).dt.strftime('%H:%M').between('10:30','11:20'))].copy()
    addl=allc[allc.candidate_no>1]
    summary=pd.DataFrame([
        dict(metric='FIRST_PER_DAY_CANDIDATES',value=len(first)),
        dict(metric='ALL_READY_CONFIRMED_CANDIDATES',value=len(allc)),
        dict(metric='ADDITIONAL_AFTER_FIRST',value=len(addl)),
        dict(metric='CURRENT_SELECTOR_SELECTED_ALL',value=len(selected)),
        dict(metric='SELECTED_ADDITIONAL_AFTER_FIRST',value=int(((allc.candidate_no>1)&allc.selected_current).sum())),
        dict(metric='SIM_TRADES',value=s['trades']),dict(metric='SIM_WINS',value=s['wins']),dict(metric='SIM_NET_PCT',value=s['net_sum_pct']),
    ])
    allc.drop(columns=['event'],errors='ignore').to_csv(OUT_DETAIL,index=False); summary.to_csv(OUT_SUMMARY,index=False)

    print('\n=== SLOW-TURN RE-ARM ABLATION ===')
    print('No strategy threshold changed. Only the first-candidate-per-day lock is removed for diagnosis.')
    print(f'First/day candidates={len(first)} | all READY+1m confirmed={len(allc)} | additional={len(addl)}')
    print(f'Current selector: selected={len(selected)} | selected additional={int(((allc.candidate_no>1)&allc.selected_current).sum())}')
    print(f"Current-selector simulation: trades={s['trades']} wins={s['wins']} WR={s['win_pct']:.2f}% net={s['net_sum_pct']:+.3f}% PF={s['pf']:.3f} maxloss={s['max_loss_pct']:+.3f}%")

    print('\n=== TARGET 950160 2026-08-14 10:30~11:20 ===')
    if target.empty:
        print('NONE')
    else:
        cols=['candidate_no','ready_time','entry_time','entry_price','mid_slope8','zero_cross_bars','gap_delta_5m','rsi_slope_5m','joint5_persistence','joint1_persistence','price_progress_1m_pct','close_progress_6m_pct','regime','selected_current','select_reason']
        print(target[cols].to_string(index=False))
    print('\nREADING: if target appears with candidate_no>1, seen_day was one blocker. If selected_current=False, select_reason identifies the second blocker.')
    print('WROTE',OUT_DETAIL)
    print('WROTE',OUT_SUMMARY)

if __name__=='__main__': main()
