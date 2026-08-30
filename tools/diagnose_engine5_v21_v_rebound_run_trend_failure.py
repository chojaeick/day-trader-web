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
OUT_EVENTS = OUT_DIR / 'v21_v_rebound_run_trend_failure_events.csv'
OUT_PATH = OUT_DIR / 'v21_v_rebound_run_trend_failure_path.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
FEE_RT_PCT = 0.25
RUN_ACTIVATION_PCT = 1.0  # descriptive only; architecture already survived 1/2/3% sensitivity


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def pct(a,b):
    a=f(a); b=f(b)
    return (b/a-1.0)*100.0 if np.isfinite(a) and a != 0 and np.isfinite(b) else np.nan


def first_hit(w, col):
    q=w[w[col].fillna(False).astype(bool)]
    if q.empty: return None
    return q.iloc[0]


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
    q=cand[(cand.stop_dist_pct<=STOP_CAP)&cand.reaccel_pass&
           (pd.to_numeric(cand.volume_accel,errors='coerce')>=VOL_MIN)&
           cand.rsi_positive_all].copy()
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first').reset_index(drop=True)

    event_rows=[]; path_rows=[]
    for _,r in q.iterrows():
        sym=n(r.symbol)
        vev,meta,_=sm.select(pd.DataFrame([r]),RAW_MIN,LEG_MIN,STOP_CAP,None)
        tr=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        if tr.empty: continue
        t=tr.iloc[0]
        et=pd.Timestamp(t.entry_time); xt=pd.Timestamp(t.exit_time); ep=f(t.entry_price)
        net=f(t.pnl_pct)-FEE_RT_PCT
        if net <= 0: continue  # RUN trend-hold is only relevant after a V has proved itself

        z=features[sym].copy()
        z=z[(z.time>=et)&(pd.to_datetime(z.time).dt.date==et.date())].copy().sort_values('time')
        if z.empty: continue
        z['close']=pd.to_numeric(z.get('px',z.get('close')),errors='coerce')
        z['low']=pd.to_numeric(z.get('lo',z.get('low')),errors='coerce')
        z['gap_delta']=pd.to_numeric(z.get('gap_delta'),errors='coerce')
        z['rsi_slope']=pd.to_numeric(z.get('rsi_slope'),errors='coerce')
        z=z[z.close.notna()].copy()
        z['ret_pct']=(z.close/ep-1.0)*100.0
        z['hwm_pct']=z.ret_pct.cummax()
        z['giveback_pct']=z.hwm_pct-z.ret_pct

        hit=z[z.ret_pct>=RUN_ACTIVATION_PCT]
        if hit.empty: continue
        run_time=pd.Timestamp(hit.iloc[0].time)
        if run_time>xt: continue
        w=z[z.time>=run_time].copy().reset_index(drop=True)

        # Causal price-structure diagnostics. All floors use only PRIOR minutes.
        w['prior3_low']=w.low.shift(1).rolling(3,min_periods=3).min()
        w['prior5_low']=w.low.shift(1).rolling(5,min_periods=5).min()
        w['break_prior3_low']=w.close < w.prior3_low
        w['break_prior5_low']=w.close < w.prior5_low
        w['close_slope3_pct']=((w.close/w.close.shift(3))-1.0)*100.0
        w['close_slope5_pct']=((w.close/w.close.shift(5))-1.0)*100.0
        w['mom_both_nonpos']=(w.gap_delta<=0)&(w.rsi_slope<=0)
        w['joint_collapse3']=w.break_prior3_low & (w.close_slope3_pct<0) & w.mom_both_nonpos
        w['joint_collapse5']=w.break_prior5_low & (w.close_slope5_pct<0) & w.mom_both_nonpos
        # Persistence: two consecutive closes below the prior-3m floor, with negative 3m path.
        b=w.break_prior3_low.fillna(False)
        w['persistent_price_break']=(b & b.shift(1,fill_value=False) & (w.close_slope3_pct<0))

        for _,rr in w.iterrows():
            path_rows.append(dict(symbol=sym,entry_time=et,time=pd.Timestamp(rr.time),ret_pct=f(rr.ret_pct),
                                  hwm_pct=f(rr.hwm_pct),giveback_pct=f(rr.giveback_pct),gap_delta=f(rr.gap_delta),
                                  rsi_slope=f(rr.rsi_slope),close_slope3_pct=f(rr.close_slope3_pct),
                                  close_slope5_pct=f(rr.close_slope5_pct),break_prior3_low=bool(rr.break_prior3_low) if pd.notna(rr.break_prior3_low) else False,
                                  break_prior5_low=bool(rr.break_prior5_low) if pd.notna(rr.break_prior5_low) else False,
                                  mom_both_nonpos=bool(rr.mom_both_nonpos),joint_collapse3=bool(rr.joint_collapse3),
                                  joint_collapse5=bool(rr.joint_collapse5),persistent_price_break=bool(rr.persistent_price_break)))

        checkpoints=[('RUN_ACTIVATION',w.iloc[0])]
        bx=w[w.time==xt]
        if len(bx): checkpoints.append(('BASELINE_EXIT',bx.iloc[0]))
        else:
            bx=w[w.time>=xt]
            if len(bx): checkpoints.append(('BASELINE_EXIT_NEXT_BAR',bx.iloc[0]))
        for label,col in [('FIRST_BREAK_3M','break_prior3_low'),('FIRST_BREAK_5M','break_prior5_low'),
                          ('FIRST_PERSISTENT_PRICE_BREAK','persistent_price_break'),
                          ('FIRST_JOINT_COLLAPSE_3M','joint_collapse3'),('FIRST_JOINT_COLLAPSE_5M','joint_collapse5')]:
            rr=first_hit(w,col)
            if rr is not None: checkpoints.append((label,rr))
        checkpoints.append(('SESSION_END',w.iloc[-1]))

        for label,rr in checkpoints:
            event_rows.append(dict(symbol=sym,entry_time=et,baseline_net_pct=net,baseline_exit_time=xt,
                                   event=label,time=pd.Timestamp(rr.time),minutes_from_entry=(pd.Timestamp(rr.time)-et).total_seconds()/60.0,
                                   ret_pct=f(rr.ret_pct),hwm_pct=f(rr.hwm_pct),giveback_pct=f(rr.giveback_pct),
                                   gap_delta=f(rr.gap_delta),rsi_slope=f(rr.rsi_slope),
                                   close_slope3_pct=f(rr.close_slope3_pct),close_slope5_pct=f(rr.close_slope5_pct)))

    events=pd.DataFrame(event_rows)
    paths=pd.DataFrame(path_rows)
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    events.to_csv(OUT_EVENTS,index=False)
    paths.to_csv(OUT_PATH,index=False)

    print('\n=== V-REBOUND RUN TREND-FAILURE DIAGNOSTIC ===')
    print('Diagnostic only. No exit rule changed.')
    print('Goal: distinguish ordinary 1m momentum fade from actual price/trend failure after RUN activation.')
    print('RUN activation shown at +1% only as a descriptive anchor; 1/2/3% architecture sensitivity already matched.')
    print('\n=== KEY EVENTS ===')
    if events.empty:
        print('NO RUN WINNER EVENTS')
    else:
        show=['symbol','event','time','ret_pct','hwm_pct','giveback_pct','gap_delta','rsi_slope','close_slope3_pct','close_slope5_pct']
        print(events[show].to_string(index=False))
    print('\nReading target:')
    print('- BASELINE_EXIT should NOT be treated as trend failure if price structure is still intact and later HWM expands.')
    print('- Prefer a causal price-structure break with persistence; momentum weakness alone is insufficient.')
    print('- If no structural failure occurs before session end, do not invent a tighter exit from this one winner.')
    print('WROTE',OUT_EVENTS)
    print('WROTE',OUT_PATH)

if __name__=='__main__': main()
