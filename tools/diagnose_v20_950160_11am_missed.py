from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

SYM='950160'
DAY=pd.Timestamp('2026-08-14').date()
RAW_MIN=52.0
REL_MIN=1.45
START=pd.Timestamp('2026-08-14 10:15:00+09:00')
END=pd.Timestamp('2026-08-14 12:30:00+09:00')


def present_times(ev):
    out=[]
    for ts in sorted(ev):
        if START <= pd.Timestamp(ts) <= END:
            for c in ev[ts]:
                if str(c[0]).zfill(6)==SYM:
                    out.append(pd.Timestamp(ts))
    return out


def main():
    raw=load_data()
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)

    frames0=base.build_cfg_frames(raw,cfg)
    f10={s:v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored=reweight(f10,cfg,0.0)
    sf={str(s).zfill(6):ms.add_strength(f) for s,f in scored.items()}

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,added,skipped=v17b.build_v17b(ev16,scored,waits)
    micro={str(s).zfill(6):h.build_micro(bb,cfg) for s,bb in raw.items()}
    ev18,vetoed=h.build_veto_stream(ev17,micro)
    ev20,diag=ms.filter_events(ev18,sf,raw_min=RAW_MIN,rel_min=REL_MIN)

    f=sf[SYM].copy(); f['time']=pd.to_datetime(f['time'])
    q=f[(f.time>=START)&(f.time<=END)].copy()
    for col in ['close','mid','inner_upper','inner_lower','outer_upper','rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_slope_spread','mid_slope8','entry_score','macd_strength_raw','macd_strength_rel']:
        if col in q.columns:q[col]=pd.to_numeric(q[col],errors='coerce')
    if 'mid' in q.columns:q['dist_mid_pct']=(q.close/q.mid-1.0)*100.0
    if 'inner_upper' in q.columns:q['dist_iu_pct']=(q.close/q.inner_upper-1.0)*100.0
    q['runup_3bar_pct']=(q.close/q.close.shift(3)-1.0)*100.0
    q['runup_6bar_pct']=(q.close/q.close.shift(6)-1.0)*100.0

    # Reconstruct the hidden V10 subconditions exactly so we can see what blocked each bar.
    full=f10[SYM].copy(); full['time']=pd.to_datetime(full['time'])
    prev_spread=pd.to_numeric(full['macd_slope_spread'],errors='coerce').shift(1)
    prev_rsi_slope=pd.to_numeric(full['rsi_slope'],errors='coerce').shift(1)
    spread_strength=pd.to_numeric(full['macd_slope_spread_strength'],errors='coerce').fillna(0.0)
    rsi_strength=pd.to_numeric(full['rsi_slope_strength'],errors='coerce').fillna(0.0)
    full['diag_macd_reaccel']=((full['macd_slope_spread']>0)&((prev_spread<=0)|(full['macd_slope_spread']>=prev_spread*0.85)|(spread_strength>=0.75))).fillna(False)
    full['diag_rsi_reaccel']=((full['rsi_slope']>0)&((prev_rsi_slope<=0)|(full['rsi_slope']>=prev_rsi_slope*0.70)|(rsi_strength>=0.75))).fillna(False)
    sub=full[['time','diag_macd_reaccel','diag_rsi_reaccel']]
    q=q.merge(sub,on='time',how='left')

    cols=['time','close','mid','inner_upper','dist_mid_pct','dist_iu_pct','runup_3bar_pct','runup_6bar_pct',
          'rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_golden_cross','macd_slope_spread','mid_slope8','trend_up',
          'gate_macd_context','gate_macd_rising','diag_macd_reaccel','diag_rsi_reaccel','gate_rsi_persistent',
          'entry_mode_continuation','entry_mode_early_reversal','entry_gate','entry_score','macd_strength_raw','macd_strength_rel']
    cols=[c for c in cols if c in q.columns]
    print('=== 950160 2026-08-14 MISSED EARLY ENTRY 10:15-12:30 / 5M ===')
    print(q[cols].to_string(index=False))

    print('\n=== PIPELINE EVENTS 10:15-12:30 ===')
    print('V10 ', present_times(ev10))
    print('V16 ', present_times(ev16))
    print('V17 ', present_times(ev17))
    print('V18 ', present_times(ev18))
    print('V20 ', present_times(ev20))

    print('\n=== V16 WAIT RECORDS ===')
    w=waits[(waits.symbol.astype(str).str.zfill(6)==SYM)&(pd.to_datetime(waits.signal_time)>=START)&(pd.to_datetime(waits.signal_time)<=END)] if len(waits) else waits
    print(w.to_string(index=False) if len(w) else 'NONE')

    print('\n=== V18 VETO RECORDS ===')
    vd=vetoed[(vetoed.symbol==SYM)&(pd.to_datetime(vetoed.time)>=START)&(pd.to_datetime(vetoed.time)<=END)] if len(vetoed) else vetoed
    print(vd.to_string(index=False) if len(vd) else 'NONE')

    print('\n=== V20 STRENGTH RECORDS ===')
    dd=diag[(diag.symbol==SYM)&(pd.to_datetime(diag.time)>=START)&(pd.to_datetime(diag.time)<=END)] if len(diag) else diag
    print(dd.to_string(index=False) if len(dd) else 'NONE')

    m=micro[SYM].copy();m['time']=pd.to_datetime(m['time'])
    qm=m[(m.time>=pd.Timestamp('2026-08-14 10:45:00+09:00'))&(m.time<=pd.Timestamp('2026-08-14 11:30:00+09:00'))]
    print('\n=== 1M 10:45-11:30 ===')
    print(qm.to_string(index=False))

if __name__=='__main__':main()
