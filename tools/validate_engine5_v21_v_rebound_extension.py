from __future__ import annotations

from dataclasses import replace
import numpy as np
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
import tools.validate_engine5_v21_v_rebound_reaccel as ra
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD=50
RAW_MIN=30.0
LEG_MIN=2.0
STOP_CAP=2.0
VOL_MIN=1.5
EXT_CAPS=[None,4.0,5.0,6.0,7.0]
TARGETS=[('950160','2026-08-14'),('950260','2026-08-19'),('950160','2026-08-21'),('950260','2026-08-20')]


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

    micros={}; allc=[]; feature_by_symbol={}
    for k,(sym,bars) in enumerate(raw.items(),1):
        print(f'[{k}/{len(raw)}] {sym}',flush=True)
        pf,m=old.load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m
        z=sm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        feature_by_symbol[sym]=z
        c=sm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): allc.append(c)

    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    base_tr=multi.simulate_multi(packed,ev20,states,THRESHOLD)
    print('\n=== BASE V20 ===')
    print(pd.DataFrame([sm.stat('V20',base_tr)]).to_string(index=False))

    cand=pd.concat(allc,ignore_index=True) if allc else pd.DataFrame()
    if cand.empty:
        print('NO V CANDIDATES'); return
    cand=ra.add_pullback_reaccel(cand,feature_by_symbol)
    cand['total_rebound_pct']=(pd.to_numeric(cand.price,errors='coerce')/pd.to_numeric(cand.base_low,errors='coerce')-1.0)*100.0

    # Freeze reaccel ON; first apply current V path structure/risk gates.
    baseq=cand[cand.reaccel_pass].copy()
    _,_,eligible=sm.select(baseq,RAW_MIN,LEG_MIN,STOP_CAP,VOL_MIN)
    if eligible.empty:
        print('NO ELIGIBLE REACCEL V CANDIDATES'); return

    print('\n=== V-REBOUND EXTENSION SWEEP ===')
    print('Fixed: RAW30 LEG2.0 STOP<=2.0% VOL>=1.5x REACCEL=ON.')
    print('Only sweep: total_rebound_pct = entry/base_low - 1. Lower cap rejects already-extended rebounds.')
    rows=[]
    for cap in EXT_CAPS:
        q=baseq.copy()
        if cap is not None:
            q=q[q.total_rebound_pct<=cap].copy()
        vev,meta,selected=sm.select(q,RAW_MIN,LEG_MIN,STOP_CAP,VOL_MIN)
        extra=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        merged=old.simulate_with_v_stop(packed,sm.merge(ev20,vev),states,THRESHOLD,meta)
        se=sm.stat('EXTRA',extra); sx=sm.stat('MERGED',merged)
        rows.append(dict(extension_cap='NONE' if cap is None else cap,signals=len(selected),**sx,
                         extra_trades=se['trades'],extra_wins=se['wins'],extra_win_pct=se['win_pct'],
                         extra_net=se['net_sum_pct'],extra_pf=se['pf'],extra_max_loss=se['max_loss_pct']))
    summary=pd.DataFrame(rows)
    print(summary.to_string(index=False))
    summary.to_csv(sm.OUT_DIR/'v21_v_rebound_extension_summary.csv',index=False)

    cols=['symbol','time','price','base_low','total_rebound_pct','structural_stop','stop_dist_pct','volume_accel','gap_delta','pullback_gap_delta','rsi_slope','pullback_rsi_slope']
    print('\n=== ELIGIBLE REACCEL SIGNALS BEFORE EXTENSION CAP ===')
    print(eligible[cols].sort_values(['time','symbol']).to_string(index=False))

    # Show which candidates each cap removes, plus actual outcomes for kept candidates.
    for cap in [4.0,5.0,6.0,7.0]:
        q=baseq[baseq.total_rebound_pct<=cap].copy()
        vev,meta,selected=sm.select(q,RAW_MIN,LEG_MIN,STOP_CAP,VOL_MIN)
        extra=old.simulate_with_v_stop(packed,vev,states,THRESHOLD,meta)
        removed=eligible[~eligible.index.isin(selected.index)]
        print(f'\n=== EXTENSION<={cap:.1f}% ===')
        print('KEPT SIGNALS:')
        print(selected[cols].sort_values(['time','symbol']).to_string(index=False) if len(selected) else 'NONE')
        print('REMOVED SIGNALS:')
        print(removed[cols].sort_values(['time','symbol']).to_string(index=False) if len(removed) else 'NONE')
        print('EXTRA OUTCOMES:')
        if len(extra):
            x=extra.copy(); x['net_after_fee']=pd.to_numeric(x.pnl_pct,errors='coerce')-old.FEE_RT_PCT
            print(x[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','net_after_fee','reason','structural_stop']].sort_values(['entry_time','symbol']).to_string(index=False))
        else: print('NONE')

    print('\n=== TARGET EXTENSION VALUES ===')
    for sym,date in TARGETS:
        q=cand[(cand.symbol==sym)&(pd.to_datetime(cand.time).dt.strftime('%Y-%m-%d')==date)].copy()
        print(f'\n{sym} {date}')
        print(q[['symbol','time','price','base_low','total_rebound_pct','stop_dist_pct','volume_accel','reaccel_pass']].sort_values('time').to_string(index=False) if len(q) else 'NONE')

    cand.drop(columns=['event']).to_csv(sm.OUT_DIR/'v21_v_rebound_extension_candidates.csv',index=False)
    print('\nWROTE v21_v_rebound_extension_candidates.csv / summary.csv')

if __name__=='__main__':main()
