from __future__ import annotations

"""Focused diagnostic for the EXISTING Slow-turn path.

Question: why did Slow-turn fail to catch 950160 on 2026-08-14 around the
chart's 10:55 turn?  No new strategy and no threshold change is introduced.
We trace the existing 5m READY gates, then the existing 1m confirmation gates,
and finally the provisional NEAR/MID/BOUNDARY selector if a candidate exists.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.validate_engine5_slow_turn_prototype as slow
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.validate_engine5_slow_turn_structure_ablation as ab
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

SYM='950160'
DAY=pd.Timestamp('2026-08-14').date()
START='10:35'; END='11:25'
OUT=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/slow_turn_950160_0814_trace.csv')


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x,errors='coerce')
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan


def main():
    raw={n(k):v for k,v in load_data().items()}
    if SYM not in raw: raise SystemExit(f'{SYM} not loaded')
    cfg=replace(DoubleBollingerEngine5Config(),macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    scored={n(s):q for s,q in reweight({n(s):v10._refine_entry_frame(x) for s,x in frames0.items()},cfg,0.0).items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}
    micro=h.build_micro(raw[SYM],cfg)
    pf,_=st.load_or_build_cache(SYM,raw[SYM],cfg,completed[SYM])
    z,m=slow.add_slow_turn_features(pf,micro)
    z['time']=pd.to_datetime(z.time); m['time']=pd.to_datetime(m.time)
    q=z[(z.time.dt.date==DAY)&(z.time.dt.strftime('%H:%M')>=START)&(z.time.dt.strftime('%H:%M')<=END)].copy()
    rows=[]
    for _,r in q.iterrows():
        ts=pd.Timestamp(r.time)
        mid=f(r.get('mid_slope8')); gain=f(r.get('slope_gain_3')); posr=f(r.get('slope_pos_ratio_3'))
        gd=f(r.get('gap_delta')); rs=f(r.get('rsi_slope'))
        gates={
            'mid_neg':np.isfinite(mid) and mid<0,
            'slope_gain':np.isfinite(gain) and gain>0,
            'slope_persist':np.isfinite(posr) and posr>=zd.SLOPE_RATIO,
            'macd_improve':np.isfinite(gd) and gd>0,
            'rsi_improve':np.isfinite(rs) and rs>0,
        }
        ready=all(gates.values())
        first_fail=next((k for k,v in gates.items() if not v),'PASS_READY')
        mr=zd.first_micro_confirmation(m,ts) if ready else None
        micro_time=pd.Timestamp(mr.time).strftime('%H:%M') if mr is not None else ''
        micro_fail=''
        if ready and mr is None:
            w=m[(m.time>=ts)&(m.time<ts+pd.Timedelta(minutes=10))].copy()
            if len(w):
                best=[]
                for _,u in w.iterrows():
                    checks=[
                        ('higher_low',bool(u.get('higher_low',False))),
                        ('high_break',bool(u.get('higher_high_break',False))),
                        ('macd_3',f(u.get('gap_pos_ratio_3'))>=zd.MICRO_RATIO),
                        ('rsi_3',f(u.get('rsi_pos_ratio_3'))>=zd.MICRO_RATIO),
                        ('macd_now',f(u.get('macd_gap_delta_1m'))>0),
                        ('rsi_now',f(u.get('rsi_slope_1m'))>0),
                    ]
                    passed=sum(v for _,v in checks)
                    fail=','.join(k for k,v in checks if not v)
                    best.append((passed,pd.Timestamp(u.time),fail))
                best.sort(key=lambda x:(-x[0],x[1]))
                micro_fail=f"best {best[0][1].strftime('%H:%M')} {best[0][0]}/6 fail={best[0][2]}"
            else: micro_fail='NO_1M_WINDOW'
        recovery=gain/3.0 if np.isfinite(gain) else np.nan
        zcb=abs(mid)/recovery if np.isfinite(recovery) and recovery>0 else np.inf
        rows.append(dict(time=ts.strftime('%H:%M'),mid_slope8=mid,slope_gain3=gain,slope_pos_ratio3=posr,
                         gap_delta_5m=gd,rsi_slope_5m=rs,ready=ready,first_fail=first_fail,
                         zero_cross_bars=zcb,micro_confirm=micro_time,micro_fail=micro_fail))
    out=pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True,exist_ok=True); out.to_csv(OUT,index=False)

    # Reconstruct actual existing Slow-turn candidates and show target-day membership.
    cand=zd.build_candidates(SYM,pf,micro,scored[SYM])
    td=cand[(pd.to_datetime(cand.ready_time).dt.date==DAY)].copy() if len(cand) else cand

    print('=== EXISTING SLOW-TURN MISS DIAGNOSTIC: 950160 2026-08-14 ===')
    print('No new strategy. No threshold changed.')
    print('Chart 10:55 can correspond to engine completed-bar 11:00; inspect both sides.')
    print('\n5M READY TRACE')
    cols=['time','mid_slope8','slope_gain3','slope_pos_ratio3','gap_delta_5m','rsi_slope_5m','ready','first_fail','zero_cross_bars','micro_confirm','micro_fail']
    print(out[cols].to_string(index=False))
    print('\nEXISTING SLOW-TURN CANDIDATES ON TARGET DAY')
    if len(td):
        c=['ready_time','entry_time','entry_price','mid_slope8','zero_cross_bars','gap_delta_5m','rsi_slope_5m','gap_pos_ratio_1m','rsi_pos_ratio_1m']
        print(td[c].to_string(index=False))
    else:
        print('NONE')
    print('\nInterpretation: first_fail identifies the 5m gate that blocks READY. If READY passes but micro_confirm is blank, micro_fail shows the closest existing 1m confirmation and missing gates.')
    print('WROTE',OUT)

if __name__=='__main__': main()
