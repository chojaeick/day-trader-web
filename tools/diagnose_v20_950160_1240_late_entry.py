from __future__ import annotations

from dataclasses import replace
from pathlib import Path
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
START=pd.Timestamp('2026-08-14 10:40:00+09:00')
END=pd.Timestamp('2026-08-14 14:05:00+09:00')
RAW_MIN=52.0
REL_MIN=1.45
OUT=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/diagnose_950160_20260814_1040_late_entry.csv')


def n(x): return str(x).zfill(6)

def finite(x):
    try:
        y=float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan

def event_keys(ev):
    out=set()
    for ts,rows in ev.items():
        t=pd.Timestamp(ts)
        if START<=t<=END:
            for c in rows:
                if n(c[0])==SYM: out.add(t)
    return out

def b(x): return bool(x) if pd.notna(x) else False


def main():
    raw=load_data()
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)

    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={n(s):h.build_micro(bb,cfg) for s,bb in raw.items()}
    ev18,vetoed=h.build_veto_stream(ev17,micros)
    ev20,diag20=ms.filter_events(ev18,strength,raw_min=RAW_MIN,rel_min=REL_MIN)

    k10,k16,k17,k18,k20=map(event_keys,[ev10,ev16,ev17,ev18,ev20])

    f=strength[SYM].copy(); f['time']=pd.to_datetime(f.time)
    q=f[(f.time>=START)&(f.time<=END)].copy().sort_values('time')

    full=f10[SYM].copy().sort_values('time'); full['time']=pd.to_datetime(full.time)
    prev_spread=pd.to_numeric(full['macd_slope_spread'],errors='coerce').shift(1)
    prev_rsi=pd.to_numeric(full['rsi_slope'],errors='coerce').shift(1)
    ss=pd.to_numeric(full['macd_slope_spread_strength'],errors='coerce').fillna(0.)
    rs=pd.to_numeric(full['rsi_slope_strength'],errors='coerce').fillna(0.)
    prev_mid=pd.to_numeric(full['mid_slope8'],errors='coerce').shift(1)
    full['diag_macd_reaccel']=((full.macd_slope_spread>0)&((prev_spread<=0)|(full.macd_slope_spread>=prev_spread*.85)|(ss>=.75))).fillna(False)
    full['diag_rsi_reaccel']=((full.rsi_slope>0)&((prev_rsi<=0)|(full.rsi_slope>=prev_rsi*.70)|(rs>=.75))).fillna(False)
    full['diag_mid_improving']=(pd.to_numeric(full.mid_slope8,errors='coerce')>prev_mid).fillna(False)
    q=q.merge(full[['time','diag_macd_reaccel','diag_rsi_reaccel','diag_mid_improving']],on='time',how='left')

    q['V10_EVENT']=q.time.isin(k10); q['V16_EVENT']=q.time.isin(k16); q['V17_EVENT']=q.time.isin(k17); q['V18_EVENT']=q.time.isin(k18); q['V20_EVENT']=q.time.isin(k20)
    q['dist_mid_pct']=(pd.to_numeric(q.close,errors='coerce')/pd.to_numeric(q.mid,errors='coerce')-1.)*100.

    def first_block(r):
        # Report the actual V10 path first. If trend_up is false, the early-reversal path may still qualify.
        if b(r.get('entry_mode_continuation')) or b(r.get('entry_mode_early_reversal')):
            pass
        else:
            if not b(r.get('trend_up')):
                if not b(r.get('diag_mid_improving')): return 'EARLY:mid_not_improving'
                if finite(r.get('macd_gap_delta'))<=0: return 'EARLY:macd_gap_delta<=0'
                if not b(r.get('diag_macd_reaccel')): return 'EARLY:macd_turn_weak'
                if not b(r.get('diag_rsi_reaccel')): return 'EARLY:rsi_turn_weak'
                return 'EARLY:price/2bar_strength'
            if not b(r.get('gate_macd_context')): return 'CONT:macd_context'
            if not b(r.get('gate_macd_rising')): return 'CONT:macd_rising'
            if not b(r.get('diag_macd_reaccel')): return 'CONT:macd_reaccel'
            if not b(r.get('diag_rsi_reaccel')): return 'CONT:rsi_reaccel'
            if not b(r.get('gate_rsi_persistent')): return 'CONT:rsi_persistent'
            return 'V10:entry_gate_other'
        if not b(r.get('entry_gate')): return 'V10:entry_gate_other'
        if not b(r.get('V10_EVENT')): return 'PACK/SCORE/RISK'
        if not b(r.get('V16_EVENT')): return 'V16_WAIT'
        if not b(r.get('V17_EVENT')): return 'V17'
        if not b(r.get('V18_EVENT')): return 'V18_1M_VETO'
        if finite(r.get('macd_strength_raw'))<RAW_MIN: return 'V20_RAW<52'
        if finite(r.get('macd_strength_rel'))<REL_MIN: return 'V20_REL<1.45'
        if not b(r.get('V20_EVENT')): return 'V20_OTHER'
        return 'PASS_V20'
    q['first_block']=q.apply(first_block,axis=1)

    cols=['time','close','dist_mid_pct','mid_slope8','diag_mid_improving','trend_up','gate_macd_context','gate_macd_rising','diag_macd_reaccel','diag_rsi_reaccel','gate_rsi_persistent','entry_mode_continuation','entry_mode_early_reversal','entry_gate','entry_score','rsi','rsi_slope','macd_gap','macd_gap_delta','macd_strength_raw','macd_strength_rel','V10_EVENT','V16_EVENT','V17_EVENT','V18_EVENT','V20_EVENT','first_block']
    cols=[c for c in cols if c in q.columns]
    OUT.parent.mkdir(parents=True,exist_ok=True); q[cols].to_csv(OUT,index=False)

    print('=== 950160 2026-08-14 10:40-14:05 EARLY/MISSED/LATE ENTRY DIAG ===')
    print('PIPELINE_FIRST:',end=' ')
    for name,ks in [('V10',k10),('V16',k16),('V17',k17),('V18',k18),('V20',k20)]:
        print(f"{name}={min(ks).strftime('%H:%M') if ks else 'NONE'}",end='  ')
    print()
    print('\n10:40-13:00 EARLY TRACE')
    brief=q[(q.time>=pd.Timestamp('2026-08-14 10:40:00+09:00'))&(q.time<=pd.Timestamp('2026-08-14 13:00:00+09:00'))][['time','close','mid_slope8','trend_up','entry_score','macd_strength_raw','macd_strength_rel','first_block']].copy()
    brief['time']=brief.time.dt.strftime('%H:%M')
    print(brief.to_string(index=False))

    print('\n13:05-14:00 REENTRY TRACE')
    brief2=q[(q.time>=pd.Timestamp('2026-08-14 13:05:00+09:00'))&(q.time<=pd.Timestamp('2026-08-14 14:00:00+09:00'))][['time','close','entry_score','macd_strength_raw','macd_strength_rel','first_block']].copy()
    brief2['time']=brief2.time.dt.strftime('%H:%M')
    print(brief2.to_string(index=False))

    print('\nV18_VETO_ROWS')
    if len(vetoed):
        vd=vetoed[(vetoed.symbol.astype(str).str.zfill(6)==SYM)&(pd.to_datetime(vetoed.time)>=START)&(pd.to_datetime(vetoed.time)<=END)]
        print(vd.to_string(index=False) if len(vd) else 'NONE')
    else: print('NONE')

    print('\nV20_STRENGTH_ROWS')
    if len(diag20):
        d=diag20[(diag20.symbol.astype(str).str.zfill(6)==SYM)&(pd.to_datetime(diag20.time)>=START)&(pd.to_datetime(diag20.time)<=END)]
        print(d.to_string(index=False) if len(d) else 'NONE')
    else: print('NONE')
    print('\nDETAIL_CSV',OUT)

if __name__=='__main__': main()
