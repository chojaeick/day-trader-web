from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

CASES=[
    ('GOOD_950160_0813','950160','2026-08-13 09:10:00+09:00','2026-08-13 09:35:00+09:00'),
    ('FAIL_257720_0818','257720','2026-08-18 14:10:00+09:00','2026-08-18 14:35:00+09:00'),
]

COLS=[
    'time','close','entry_score','entry_gate','trend_up','gate_macd_rising','gate_macd_accel','gate_macd_context','gate_rsi_persistent',
    'macd','macd_signal','macd_slope','macd_signal_slope','macd_slope_spread','rsi','rsi_slope','mid_slope8','outer_width_ratio','volume_ratio',
    'golden_cross','macd_above_signal','inner_traverse_up','outer_expanding'
]


def fnum(x):
    try:
        v=float(x); return v if np.isfinite(v) else np.nan
    except Exception:return np.nan


def enrich(f:pd.DataFrame)->pd.DataFrame:
    z=f.copy().sort_values('time').reset_index(drop=True)
    z['prev_macd']=pd.to_numeric(z['macd'],errors='coerce').shift(1)
    z['prev_signal']=pd.to_numeric(z['macd_signal'],errors='coerce').shift(1)
    z['macd_ratio']=pd.to_numeric(z['macd'],errors='coerce')/z['prev_macd'].replace(0,np.nan)
    z['signal_ratio']=pd.to_numeric(z['macd_signal'],errors='coerce')/z['prev_signal'].replace(0,np.nan)
    z['ratio_edge']=z['macd_ratio']-z['signal_ratio']
    z['gap']=pd.to_numeric(z['macd'],errors='coerce')-pd.to_numeric(z['macd_signal'],errors='coerce')
    z['gap_delta']=z['gap'].diff()
    z['prev_rsi']=pd.to_numeric(z['rsi'],errors='coerce').shift(1)
    z['rsi_cross50']=(z['prev_rsi']<50)&(pd.to_numeric(z['rsi'],errors='coerce')>=50)
    z['rsi_cross70']=(z['prev_rsi']<70)&(pd.to_numeric(z['rsi'],errors='coerce')>=70)
    return z


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    raw_frames=base.build_cfg_frames(raw,cfg)
    refined={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}
    scored=reweight(refined,cfg,0.0)
    print('=== V17C ENTRY-GATE DIAGNOSTIC ===')
    print('Purpose: explain why 950160 was not taken earlier and why flat/parallel MACD-Signal cases can still pass.')
    print('No strategy change. 5m timestamps are engine end-labels.')
    for label,sym,a,b in CASES:
        print(f'\n=== {label} {sym} ===')
        f=enrich(scored[sym])
        q=f[(pd.to_datetime(f.time)>=pd.Timestamp(a))&(pd.to_datetime(f.time)<=pd.Timestamp(b))].copy()
        wanted=[c for c in COLS+['macd_ratio','signal_ratio','ratio_edge','gap','gap_delta','rsi_cross50','rsi_cross70'] if c in q.columns]
        print(q[wanted].to_string(index=False))
        print('\nFAILED GATES / ENTRY CANDIDATES')
        for r in q.itertuples(index=False):
            d=r._asdict(); fails=[]
            for c in ['trend_up','gate_macd_rising','gate_macd_accel','gate_macd_context','gate_rsi_persistent']:
                if c in d and not bool(d[c]): fails.append(c)
            print(pd.Timestamp(d['time']), 'score=',fnum(d.get('entry_score')), 'entry_gate=',d.get('entry_gate'), 'fails=',fails,
                  'MACD/Signal=',fnum(d.get('macd')),fnum(d.get('macd_signal')),
                  'ratio_edge=',fnum(d.get('ratio_edge')),'gap_delta=',fnum(d.get('gap_delta')),
                  'RSI=',fnum(d.get('rsi')),'RSI_slope=',fnum(d.get('rsi_slope')))

if __name__=='__main__':main()
