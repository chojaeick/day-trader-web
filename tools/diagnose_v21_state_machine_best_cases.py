from __future__ import annotations

from dataclasses import replace
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v21_v_rebound_structural_stop as old
import tools.validate_engine5_v21_v_rebound_state_machine as sm
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD=50
CONFIGS=[
    ('BEST_1P5',30.0,2.0,1.5,1.5),
    ('TARGET_ALLOW_2P0',30.0,2.0,2.0,1.5),
]


def n(x): return str(x).zfill(6)


def main():
    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg)
    states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(x) for s,x in frames.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}
    strength={s:ms.add_strength(x) for s,x in scored.items()}
    completed={s:rt.add_completed_strength(x) for s,x in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)

    micros={}; allc=[]
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True)
        pf,m=old.load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m
        z=sm.add_features(pf,m,bars)
        for raw_min in [30.0]:
            for leg in [2.0]:
                c=sm.state_candidates(sym,z,scored[sym],raw_min,leg)
                if len(c): allc.append(c)

    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO CANDIDATES'); return

    print('\n=== FOCUSED CONFIG COMPARISON ===')
    for label,raw_min,leg,cap,vol in CONFIGS:
        vev,meta,q=sm.select(cand,raw_min,leg,cap,vol)
        extra=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        merged=old.simulate_with_v_stop(packed,sm.merge(ev20,vev),states,THRESHOLD,meta)
        print(f'\n--- {label}: RAW{raw_min:.0f} LEG{leg:.2f} STOP<={cap:.1f}% VOL>={vol:.1f}x ---')
        print('EXTRA_STATS',pd.DataFrame([sm.stat('EXTRA',extra)]).to_string(index=False))
        print('MERGED_STATS',pd.DataFrame([sm.stat('MERGED',merged)]).to_string(index=False))
        cols=['symbol','time','price','structural_stop','stop_dist_pct','volume_accel','base_low','first_rebound_high','first_rebound_high_time','pullback_start','gap_delta','rsi_slope']
        print('\nSELECTED SIGNALS:')
        print(q[cols].sort_values(['time','symbol']).to_string(index=False) if len(q) else 'NONE')
        print('\nEXTRA TRADE OUTCOMES:')
        if len(extra):
            x=extra.copy(); x['net_after_fee']=pd.to_numeric(x.pnl_pct,errors='coerce')-old.FEE_RT_PCT
            print(x[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','net_after_fee','reason','structural_stop']].sort_values(['entry_time','symbol']).to_string(index=False))
        else: print('NONE')
        print('\nTARGET OUTCOMES:')
        for sym,date in [('950160','2026-08-14'),('950260','2026-08-19')]:
            qq=q[(q.symbol==sym)&(pd.to_datetime(q.time).dt.strftime('%Y-%m-%d')==date)]
            tt=extra[(extra.symbol.astype(str).str.zfill(6)==sym)&(pd.to_datetime(extra.entry_time).dt.strftime('%Y-%m-%d')==date)] if len(extra) else pd.DataFrame()
            print(sym,date,'SIGNAL=',not qq.empty,'TRADE=',not tt.empty)
            if len(qq): print(qq[cols].to_string(index=False))
            if len(tt):
                y=tt.copy(); y['net_after_fee']=pd.to_numeric(y.pnl_pct,errors='coerce')-old.FEE_RT_PCT
                print(y[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','net_after_fee','reason','structural_stop']].to_string(index=False))

if __name__=='__main__': main()
