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
OUT_CASES = OUT_DIR / 'v21_v_rebound_close_path_momentum_cases.csv'
OUT_GROUP = OUT_DIR / 'v21_v_rebound_close_path_momentum_group.csv'

THRESHOLD = 50
RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
FEE_RT_PCT = 0.25


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y=float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def pct(a,b):
    a=f(a); b=f(b)
    return (b/a-1.0)*100.0 if np.isfinite(a) and a != 0 and np.isfinite(b) else np.nan

def slope(vals):
    y=pd.to_numeric(pd.Series(vals),errors='coerce').dropna().to_numpy(dtype=float)
    if len(y)<2: return np.nan
    x=np.arange(len(y),dtype=float)
    return float(np.polyfit(x,y,1)[0])

def max_consecutive_true(s):
    best=cur=0
    for v in pd.Series(s).fillna(False).astype(bool):
        cur=cur+1 if v else 0
        best=max(best,cur)
    return int(best)

def stat(label,g):
    if g.empty:
        return dict(label=label,trades=0,wins=0,net_sum=0.0,below_close_3m_mean=np.nan,below_close_5m_mean=np.nan,max_below_streak_5m_mean=np.nan,close_slope_3m_mean=np.nan,close_slope_5m_mean=np.nan,joint_weak_3m_mean=np.nan,joint_weak_5m_mean=np.nan)
    net=pd.to_numeric(g.net_pct,errors='coerce')
    return dict(
        label=label,trades=len(g),wins=int((net>0).sum()),net_sum=float(net.sum()),
        below_close_3m_mean=float(pd.to_numeric(g.below_close_count_3m,errors='coerce').mean()),
        below_close_5m_mean=float(pd.to_numeric(g.below_close_count_5m,errors='coerce').mean()),
        max_below_streak_5m_mean=float(pd.to_numeric(g.max_below_close_streak_5m,errors='coerce').mean()),
        close_slope_3m_mean=float(pd.to_numeric(g.close_ret_slope_3m,errors='coerce').mean()),
        close_slope_5m_mean=float(pd.to_numeric(g.close_ret_slope_5m,errors='coerce').mean()),
        joint_weak_3m_mean=float(pd.to_numeric(g.joint_weak_count_3m,errors='coerce').mean()),
        joint_weak_5m_mean=float(pd.to_numeric(g.joint_weak_count_5m,errors='coerce').mean()),
    )


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
    if q.empty:
        print('NO COHORT'); return

    rows=[]
    for _,r in q.iterrows():
        sym=n(r.symbol); ts=pd.Timestamp(r.time); ep=f(r.price)
        vev,meta,_=sm.select(pd.DataFrame([r]),RAW_MIN,LEG_MIN,STOP_CAP,None)
        tr=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        if tr.empty: continue
        t=tr.iloc[0]; net=f(t.pnl_pct)-FEE_RT_PCT
        z=features[sym].copy()
        close_col='px' if 'px' in z.columns else 'close'
        zz=z[(z.time>=ts)&(z.time<=ts+pd.Timedelta(minutes=5))].copy().sort_values('time')
        if zz.empty: continue
        zz['close']=pd.to_numeric(zz[close_col],errors='coerce')
        zz['close_ret_pct']=[pct(ep,v) for v in zz.close]
        zz['gap_delta']=pd.to_numeric(zz.gap_delta,errors='coerce')
        zz['rsi_slope']=pd.to_numeric(zz.rsi_slope,errors='coerce')
        entry_gap=f(zz.iloc[0].gap_delta); entry_rsi=f(zz.iloc[0].rsi_slope)
        zz['below_entry_close']=zz.close<ep
        zz['joint_weaker']=(zz.gap_delta<entry_gap)&(zz.rsi_slope<entry_rsi)
        zz['below_and_joint_weaker']=zz.below_entry_close & zz.joint_weaker
        w3=zz[zz.time<=ts+pd.Timedelta(minutes=3)]
        w5=zz[zz.time<=ts+pd.Timedelta(minutes=5)]
        rows.append(dict(
            result='WIN' if net>0 else 'LOSS',symbol=sym,entry_time=ts,net_pct=net,
            reason=t.get('reason',''),gap_keep_ratio=f(r.gap_keep_ratio),stop_dist_pct=f(r.stop_dist_pct),
            entry_gap_delta=entry_gap,entry_rsi_slope=entry_rsi,
            below_close_count_3m=int(w3.below_entry_close.sum()),
            below_close_count_5m=int(w5.below_entry_close.sum()),
            max_below_close_streak_5m=max_consecutive_true(w5.below_entry_close),
            close_ret_end_1m=f(w5.iloc[min(1,len(w5)-1)].close_ret_pct),
            close_ret_end_2m=f(w5.iloc[min(2,len(w5)-1)].close_ret_pct),
            close_ret_end_3m=f(w5.iloc[min(3,len(w5)-1)].close_ret_pct),
            close_ret_end_5m=f(w5.iloc[-1].close_ret_pct),
            close_ret_slope_3m=slope(w3.close_ret_pct),
            close_ret_slope_5m=slope(w5.close_ret_pct),
            joint_weak_count_3m=int(w3.joint_weaker.sum()),
            joint_weak_count_5m=int(w5.joint_weaker.sum()),
            below_joint_weak_count_3m=int(w3.below_and_joint_weaker.sum()),
            below_joint_weak_count_5m=int(w5.below_and_joint_weaker.sum()),
        ))

    x=pd.DataFrame(rows).sort_values('entry_time')
    if x.empty:
        print('NO SIMULATED CASES'); return
    group=pd.DataFrame([stat('LOSS',x[x.net_pct<=0]),stat('WIN',x[x.net_pct>0]),stat('ALL',x)])
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    x.to_csv(OUT_CASES,index=False); group.to_csv(OUT_GROUP,index=False)

    print('\n=== V-REBOUND CLOSE-PATH + MOMENTUM DIAGNOSTIC ===')
    print('Diagnostic only. Entry/exit rules unchanged.')
    print('Cohort: STOP<=2, REACCEL, VOL>=1.0, RSI positive through pullback; GAP_KEEP not filtered.')
    print('\n=== GROUP SUMMARY ===')
    print(group.to_string(index=False))
    print('\n=== CASES ===')
    cols=['result','symbol','entry_time','net_pct','gap_keep_ratio','stop_dist_pct',
          'below_close_count_3m','below_close_count_5m','max_below_close_streak_5m',
          'close_ret_end_1m','close_ret_end_2m','close_ret_end_3m','close_ret_end_5m',
          'close_ret_slope_3m','close_ret_slope_5m','joint_weak_count_3m','joint_weak_count_5m',
          'below_joint_weak_count_3m','below_joint_weak_count_5m','reason']
    print(x[cols].to_string(index=False))
    print('\nReading target:')
    print('- A useful early-failure structure should keep the big winner while identifying losers by persistent below-entry closes, negative close-path slope, or price+momentum weakness together.')
    print('- Do not freeze a numeric threshold if the winner overlaps materially with losses.')
    print('- If separation remains weak, keep the existing Higher-Low structural stop and stop adding early-exit complexity.')
    print('WROTE',OUT_CASES)
    print('WROTE',OUT_GROUP)

if __name__=='__main__': main()
