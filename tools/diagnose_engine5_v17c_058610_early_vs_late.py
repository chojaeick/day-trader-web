from __future__ import annotations
from dataclasses import replace
import pandas as pd
import numpy as np

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

SYM='058610'
START=pd.Timestamp('2026-08-11 09:10:00+09:00')
END=pd.Timestamp('2026-08-11 10:10:00+09:00')


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f=v10._refine_entry_frame(frames0[SYM]).copy().sort_values('time').reset_index(drop=True)
    f=reweight({SYM:f},cfg,0.0)[SYM]
    for c in ['macd','macd_signal','macd_slope','macd_signal_slope','macd_slope_spread','rsi','rsi_slope','mid_slope8','outer_width_ratio','volume_ratio','entry_score']:
        if c in f.columns: f[c]=pd.to_numeric(f[c],errors='coerce')
    f['gap']=f['macd']-f['macd_signal']
    f['gap_delta']=f['gap'].diff()
    f['gap_delta_prev']=f['gap_delta'].shift(1)
    f['gap_accel']=f['gap_delta']-f['gap_delta_prev']
    f['rsi_prev']=f['rsi'].shift(1)
    f['rsi_cross50']=(f['rsi_prev']<50)&(f['rsi']>=50)
    f['rsi_cross70']=(f['rsi_prev']<70)&(f['rsi']>=70)
    q=f[(pd.to_datetime(f['time'])>=START)&(pd.to_datetime(f['time'])<=END)].copy()
    cols=['time','close','entry_score','entry_gate','trend_up','gate_macd_rising','gate_macd_accel','gate_macd_context','gate_rsi_persistent','macd','macd_signal','macd_slope','macd_signal_slope','macd_slope_spread','gap','gap_delta','gap_accel','rsi','rsi_slope','rsi_cross50','rsi_cross70','mid_slope8','outer_width_ratio','volume_ratio','outer_expanding']
    cols=[c for c in cols if c in q.columns]
    print('=== 058610 2026-08-11 EARLY VS LATE ENTRY DIAGNOSTIC ===')
    print('No strategy change. 5m engine bars, end-labeled.')
    print(q[cols].to_string(index=False))
    print('\n=== FIRST TIMES BY CONDITION ===')
    tests={
      'MACD_GAP_EXPANDING': q['gap_delta']>0,
      'MACD_GAP_EXPANDING_2_BARS': (q['gap_delta']>0)&(q['gap_delta'].shift(1)>0),
      'MACD_SLOPE_GT_SIGNAL': q['macd_slope']>q['macd_signal_slope'],
      'RSI_RISING_GT50': (q['rsi']>50)&(q['rsi_slope']>0),
      'RSI_RISING_GT70': (q['rsi']>70)&(q['rsi_slope']>0),
      'TREND_UP': q['trend_up'].fillna(False),
      'ENTRY_GATE': q['entry_gate'].fillna(False),
    }
    for name,mask in tests.items():
        hit=q[mask]
        print(name, '=>', (pd.Timestamp(hit.iloc[0]['time']) if len(hit) else 'NONE'))
    print('\n=== LATE/DECEL FLAGS ===')
    qq=q.copy()
    qq['gap_delta_falling']=(qq['gap_delta']>0)&(qq['gap_delta']<qq['gap_delta'].shift(1))
    qq['rsi_falling']=qq['rsi_slope']<0
    show=qq[qq['entry_gate'].fillna(False)][['time','close','entry_score','gap_delta','gap_accel','rsi','rsi_slope','gap_delta_falling','rsi_falling']]
    print(show.to_string(index=False) if len(show) else 'NO_ENTRY_GATE')

if __name__=='__main__': main()
