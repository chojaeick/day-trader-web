from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

CASES=[
    ('SUCCESS','950260','2026-08-20','13:19'),
    ('FAIL','950160','2026-08-11','09:36'),
    ('FAIL','080220','2026-08-13','14:01'),
    ('FAIL','950160','2026-08-21','09:47'),
    ('FAIL_TARGET','950260','2026-08-19','13:35'),
]


def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan


def main():
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    frames=v10.base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}

    cache={}
    for sym in sorted(set(x[1] for x in CASES)):
        pf,m=old.load_cache(sym,raw[sym],cfg,completed[sym])
        cache[sym]=sm.add_features(pf,m,raw[sym]).sort_values('time').reset_index(drop=True)

    print('=== V21 V-REBOUND CONTINUITY CASE COMPARISON ===')
    print('Window = entry -10m through entry +5m. Future rows are diagnostic only, NOT entry inputs.')
    print('dGap=1m MACD-gap delta; gap3=sum last 3 dGap; gapPos3=positive dGap count/3; rsiPos3=positive RSI-slope count/3; vol=3m/prior10m volume ratio.')

    summaries=[]
    for label,sym,date,hm in CASES:
        z=cache[sym].copy(); z['time']=pd.to_datetime(z.time)
        tz=z.time.dt.tz
        entry=pd.Timestamp(f'{date} {hm}')
        if tz is not None: entry=entry.tz_localize(tz)
        q=z[(z.time>=entry-pd.Timedelta(minutes=10))&(z.time<=entry+pd.Timedelta(minutes=5))].copy()
        if q.empty:
            print(f'\n{label} {sym} {date} {hm}: NO ROWS'); continue
        gd=pd.to_numeric(q.gap_delta,errors='coerce')
        rs=pd.to_numeric(q.rsi_slope,errors='coerce')
        q['gap3']=gd.rolling(3,min_periods=1).sum()
        q['gapPos3']=(gd>0).rolling(3,min_periods=1).mean()
        q['rsiPos3']=(rs>0).rolling(3,min_periods=1).mean()
        px=pd.to_numeric(q.close,errors='coerce')
        q['pxRet1']=px.pct_change()*100
        q['pxPos3']=(q.pxRet1>0).rolling(3,min_periods=1).mean()
        q['phase']=np.where(q.time<entry,'PRE',np.where(q.time==entry,'ENTRY','POST'))
        cols=['time','phase','close','pxRet1','gap_delta','gap3','gapPos3','rsi','rsi_slope','rsiPos3','mid_slope8','slope_gain3','strength_rel','volume_accel_3v10','pxPos3']
        print(f'\n--- {label} {sym} {date} ENTRY {hm} ---')
        print(q[cols].to_string(index=False,formatters={
            'pxRet1':lambda x:f'{x:+.3f}' if pd.notna(x) else 'nan',
            'gap_delta':lambda x:f'{x:+.2f}' if pd.notna(x) else 'nan',
            'gap3':lambda x:f'{x:+.2f}' if pd.notna(x) else 'nan',
            'rsi_slope':lambda x:f'{x:+.2f}' if pd.notna(x) else 'nan',
            'mid_slope8':lambda x:f'{x:+.2f}' if pd.notna(x) else 'nan',
            'slope_gain3':lambda x:f'{x:+.2f}' if pd.notna(x) else 'nan',
            'strength_rel':lambda x:f'{x:.2f}' if pd.notna(x) else 'nan',
            'volume_accel_3v10':lambda x:f'{x:.2f}' if pd.notna(x) else 'nan',
        }))
        pre=q[q.time<=entry].tail(5)
        er=q[q.time==entry]
        if len(er):
            e=er.iloc[-1]
            summaries.append(dict(case=label,symbol=sym,entry=entry,
                gap_delta=f(e.gap_delta),gap3=f(e.gap3),gapPos3=f(e.gapPos3),
                rsi_slope=f(e.rsi_slope),rsiPos3=f(e.rsiPos3),pxPos3=f(e.pxPos3),
                mid_slope8=f(e.mid_slope8),slope_gain3=f(e.slope_gain3),rel=f(e.strength_rel),vol=f(e.volume_accel_3v10),
                pre5_gap_pos=float((pd.to_numeric(pre.gap_delta,errors='coerce')>0).mean()),
                pre5_rsi_pos=float((pd.to_numeric(pre.rsi_slope,errors='coerce')>0).mean()),
                pre5_px_pos=float((pd.to_numeric(pre.close,errors='coerce').pct_change()>0).mean())))
    if summaries:
        print('\n=== ENTRY-TIME SUMMARY ===')
        print(pd.DataFrame(summaries).to_string(index=False))
        print('\nInterpretation target: find conditions present in SUCCESS but absent from failures using PRE/ENTRY rows only. POST is shown only to verify follow-through/failure after the fact.')

if __name__=='__main__':main()
