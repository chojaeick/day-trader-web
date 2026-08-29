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
TARGET=pd.Timestamp('2026-08-14 13:55:00+09:00')
RAW_MIN=52.0
REL_MIN=1.45


def b(x):
    try: return bool(x)
    except Exception: return False


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

    f=sf[SYM].copy()
    f['time']=pd.to_datetime(f['time'])
    q=f[(f.time.dt.date==DAY)&(f.time>=pd.Timestamp('2026-08-14 12:30:00+09:00'))&(f.time<=pd.Timestamp('2026-08-14 14:15:00+09:00'))].copy()

    # Useful derived context: how stretched price is relative to DBB and recent run-up.
    for col in ['close','mid','inner_upper','inner_lower','outer_upper','rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_slope_spread','mid_slope8','entry_score','macd_strength_raw','macd_strength_rel']:
        if col in q.columns: q[col]=pd.to_numeric(q[col],errors='coerce')
    if 'mid' in q.columns:
        q['dist_mid_pct']=(q.close/q.mid-1.0)*100.0
    if 'inner_upper' in q.columns:
        q['dist_iu_pct']=(q.close/q.inner_upper-1.0)*100.0
    q['runup_3bar_pct']=(q.close/q.close.shift(3)-1.0)*100.0
    q['runup_6bar_pct']=(q.close/q.close.shift(6)-1.0)*100.0

    cols=['time','close','mid','inner_upper','outer_upper','dist_mid_pct','dist_iu_pct','runup_3bar_pct','runup_6bar_pct',
          'rsi','rsi_slope','macd','macd_signal','macd_gap','macd_gap_delta','macd_slope_spread','mid_slope8','trend_up',
          'gate_macd_context','gate_macd_rising','gate_rsi_persistent','entry_mode_continuation','entry_mode_early_reversal','entry_gate','entry_score',
          'macd_strength_raw','macd_strength_rel','macd_golden_cross']
    cols=[c for c in cols if c in q.columns]
    print('=== 950160 2026-08-14 5M CONTEXT 12:30-14:15 ===')
    print(q[cols].to_string(index=False))

    m=micro[SYM].copy(); m['time']=pd.to_datetime(m['time'])
    qm=m[(m.time>=pd.Timestamp('2026-08-14 13:45:00+09:00'))&(m.time<=pd.Timestamp('2026-08-14 14:05:00+09:00'))].copy()
    print('\n=== 1M AROUND TOP ENTRY 13:45-14:05 ===')
    print(qm.to_string(index=False))

    print('\n=== PIPELINE PRESENCE AT 13:55 ===')
    def present(ev):
        return any(str(c[0]).zfill(6)==SYM for c in ev.get(TARGET,[]))
    print('V10_EVENT',present(ev10))
    print('V16_EVENT',present(ev16))
    print('V17_EVENT',present(ev17))
    print('V18_EVENT',present(ev18))
    print('V20_EVENT',present(ev20))

    vd=vetoed[(vetoed.symbol==SYM)&(pd.to_datetime(vetoed.time)==TARGET)] if len(vetoed) else vetoed
    print('\nV18_VETO_RECORD_AT_TARGET')
    print(vd.to_string(index=False) if len(vd) else 'NONE -> V18 did not veto target')

    dd=diag[(diag.symbol==SYM)&(pd.to_datetime(diag.time)==TARGET)] if len(diag) else diag
    print('\nV20_STRENGTH_RECORD_AT_TARGET')
    print(dd.to_string(index=False) if len(dd) else 'NONE')

    r=q[q.time==TARGET]
    if len(r):
        rr=r.iloc[-1]
        print('\n=== TARGET SUMMARY ===')
        for k in ['close','mid','inner_upper','outer_upper','dist_mid_pct','dist_iu_pct','runup_3bar_pct','runup_6bar_pct','rsi','rsi_slope','macd_gap','macd_gap_delta','macd_slope_spread','mid_slope8','trend_up','entry_mode_continuation','entry_mode_early_reversal','entry_gate','entry_score','macd_strength_raw','macd_strength_rel']:
            if k in r.columns: print(k,rr[k])

if __name__=='__main__':
    main()
