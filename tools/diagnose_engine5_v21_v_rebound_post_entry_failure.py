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
OUT_CASES = OUT_DIR / 'v21_v_rebound_post_entry_failure_cases.csv'
OUT_MINUTES = OUT_DIR / 'v21_v_rebound_post_entry_failure_minutes.csv'
OUT_TRIGGERS = OUT_DIR / 'v21_v_rebound_post_entry_failure_triggers.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
GAP_KEEP = 0.9
FEE_RT_PCT = 0.25


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan


def ret_pct(a,b):
    a=f(a); b=f(b)
    return (b/a-1.0)*100.0 if np.isfinite(a) and a != 0 and np.isfinite(b) else np.nan


def first_two_consecutive(w, mask_col):
    s = w[mask_col].fillna(False).astype(bool)
    hit = s & s.shift(1, fill_value=False)
    if not hit.any(): return pd.NaT
    return pd.Timestamp(w.loc[hit.idxmax(), 'time'])


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
    if cand.empty: print('NO CANDIDATES'); return
    cand=ra.add_pullback_reaccel(cand,features)
    cand=mp.add_preservation(cand,features)
    q=cand[(cand.stop_dist_pct<=STOP_CAP)&cand.reaccel_pass&
           (pd.to_numeric(cand.volume_accel,errors='coerce')>=VOL_MIN)&
           cand.rsi_positive_all&
           (pd.to_numeric(cand.gap_keep_ratio,errors='coerce')>=GAP_KEEP)].copy()
    q['day']=pd.to_datetime(q.time).dt.date
    q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first').reset_index(drop=True)
    vev,meta,qsel=sm.select(q,RAW_MIN,LEG_MIN,STOP_CAP,None)
    tr=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
    if tr.empty: print('NO TRADES'); return
    tr['net_pct']=pd.to_numeric(tr.pnl_pct,errors='coerce')-FEE_RT_PCT

    minute_rows=[]; case_rows=[]; trigger_rows=[]
    for _,t in tr.iterrows():
        sym=n(t.symbol); et=pd.Timestamp(t.entry_time); ep=f(t.entry_price)
        z=features[sym]
        w=z[(z.time>=et)&(z.time<=et+pd.Timedelta(minutes=10))].copy().sort_values('time')
        if w.empty: continue
        w['px']=pd.to_numeric(w.get('px',w.get('close')),errors='coerce')
        w['gap_delta']=pd.to_numeric(w.gap_delta,errors='coerce')
        w['rsi_slope']=pd.to_numeric(w.rsi_slope,errors='coerce')
        eg=f(w.iloc[0].gap_delta); er=f(w.iloc[0].rsi_slope)
        w['ret_from_entry_pct']=[ret_pct(ep,v) for v in w.px]
        w['both_negative']=(w.gap_delta<=0)&(w.rsi_slope<=0)
        w['below_entry_both_weaker']=(w.px<ep)&(w.gap_delta<eg)&(w.rsi_slope<er)
        w['below_entry_both_nonpos']=(w.px<ep)&(w.gap_delta<=0)&(w.rsi_slope<=0)
        w['symbol']=sym; w['entry_time']=et; w['net_pct']=f(t.net_pct)
        minute_rows.append(w[['symbol','entry_time','time','px','ret_from_entry_pct','gap_delta','rsi_slope','both_negative','below_entry_both_weaker','below_entry_both_nonpos','net_pct']])

        mfe=f(w.ret_from_entry_pct.max()); mae=f(w.ret_from_entry_pct.min())
        c=dict(symbol=sym,entry_time=et,entry_price=ep,exit_time=pd.Timestamp(t.exit_time),
               exit_price=f(t.exit_price),net_pct=f(t.net_pct),reason=t.get('reason',''),
               entry_gap_delta=eg,entry_rsi_slope=er,mfe_10m_pct=mfe,mae_10m_pct=mae,
               ret_2m_pct=f(w.iloc[min(2,len(w)-1)].ret_from_entry_pct),
               ret_5m_pct=f(w.iloc[min(5,len(w)-1)].ret_from_entry_pct),
               end10_ret_pct=f(w.iloc[-1].ret_from_entry_pct))
        case_rows.append(c)

        for label,col in [
            ('BOTH_NEGATIVE_2M','both_negative'),
            ('BELOW_ENTRY_BOTH_WEAKER_2M','below_entry_both_weaker'),
            ('BELOW_ENTRY_BOTH_NONPOS_2M','below_entry_both_nonpos')]:
            ts=first_two_consecutive(w,col)
            if pd.isna(ts):
                trigger_rows.append(dict(symbol=sym,entry_time=et,net_pct=f(t.net_pct),trigger=label,triggered=False,trigger_time=pd.NaT,minutes_after_entry=np.nan,trigger_ret_pct=np.nan))
            else:
                rr=w[w.time.eq(ts)].iloc[0]
                trigger_rows.append(dict(symbol=sym,entry_time=et,net_pct=f(t.net_pct),trigger=label,triggered=True,trigger_time=ts,minutes_after_entry=(ts-et).total_seconds()/60.0,trigger_ret_pct=f(rr.ret_from_entry_pct)))

    cases=pd.DataFrame(case_rows).sort_values('entry_time')
    mins=pd.concat(minute_rows,ignore_index=True) if minute_rows else pd.DataFrame()
    triggers=pd.DataFrame(trigger_rows)
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    cases.to_csv(OUT_CASES,index=False); mins.to_csv(OUT_MINUTES,index=False); triggers.to_csv(OUT_TRIGGERS,index=False)

    print('\n=== V REBOUND POST-ENTRY FAILURE DIAGNOSTIC ===')
    print('Descriptive only. Entry rules and exit rules are unchanged.')
    print('\n=== CASE SUMMARY ===')
    show=['symbol','entry_time','net_pct','reason','entry_gap_delta','entry_rsi_slope','mfe_10m_pct','mae_10m_pct','ret_2m_pct','ret_5m_pct','end10_ret_pct']
    print(cases[show].to_string(index=False))
    print('\n=== 2-MINUTE FAILURE TRIGGER COMPARISON ===')
    print(triggers.to_string(index=False))
    print('\nReading target:')
    print('- A useful V-specific early-failure rule should trigger on the two small losers early, but not on the large winner before it expands.')
    print('- One weak minute is deliberately NOT treated as failure.')
    print('- If none of these simple 2-minute states separates them, do not force an exit threshold; inspect price structure next.')
    print('WROTE',OUT_CASES)
    print('WROTE',OUT_MINUTES)
    print('WROTE',OUT_TRIGGERS)

if __name__=='__main__': main()
