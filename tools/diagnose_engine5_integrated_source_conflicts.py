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
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_slow_turn_provisional_full as slowfull
import tools.validate_engine5_slow_turn_regime_integrated as ri
import tools.validate_engine5_slow_turn_structure_ablation as ab
import tools.validate_engine5_v21_v_rebound_structural_stop as vold
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT = OUT_DIR / 'integrated_source_conflicts.csv'
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'

RAW_MIN = 30.0
LEG_MIN = 2.0
STOP_CAP = 2.0
VOL_MIN = 1.0
GAP_KEEP_MIN = 0.9


def n(x): return str(x).zfill(6)


def keys_from_events(ev, source):
    rows=[]
    for ts, cs in ev.items():
        for c in cs:
            rows.append(dict(source=source, symbol=n(c[0]), time=pd.Timestamp(ts)))
    return pd.DataFrame(rows)


def main():
    if not PERSIST_SRC.exists():
        raise FileNotFoundError(f'{PERSIST_SRC} missing; run slow-turn persistence diagnostic first')

    raw={n(k):v for k,v in load_data().items()}
    base_cfg=DoubleBollingerEngine5Config()
    cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}

    # Build protected V20 stream.
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)

    micros={}
    provisional={}
    vfeatures={}
    vall=[]
    for sym,bars in raw.items():
        pf,m=vold.load_cache(sym,bars,cfg,completed[sym])
        micros[sym]=m
        provisional[sym]=pf
        z=vsm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True)
        vfeatures[sym]=z
        c=vsm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): vall.append(c)

    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=52.0,rel_min=1.45)

    # Reconstruct current provisional gradual-turn selection.
    base_cand=ri.reconstruct_base_candidates(raw,cfg,scored,completed,micros)
    base_cand['symbol']=base_cand.symbol.astype(str).str.zfill(6)
    base_cand['entry_time']=pd.to_datetime(base_cand.entry_time)
    persist=pd.read_csv(PERSIST_SRC)
    persist['symbol']=persist.symbol.astype(str).str.zfill(6)
    persist['entry_time']=pd.to_datetime(persist.entry_time)
    keep=['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    sx=base_cand.merge(persist[keep],on=['symbol','entry_time'],how='inner',validate='one_to_one')
    ext=[]
    for _,r in sx.iterrows():
        ext.append(ab.metric_window(micros[n(r.symbol)],pd.Timestamp(r.entry_time)))
    sx=pd.concat([sx.reset_index(drop=True),pd.DataFrame(ext)],axis=1)
    smask=[]; sreg=[]
    for _,r in sx.iterrows():
        ok,rg=slowfull.classify_and_select(r); smask.append(ok); sreg.append(rg)
    sx['regime']=sreg
    slow_sel=sx[np.asarray(smask,dtype=bool)].copy()
    slow_keys=slow_sel[['symbol','entry_time']].rename(columns={'entry_time':'time'}).copy()
    slow_keys['source']='SLOW_TURN'

    # Current selected V cohort: stop<=2, reaccel, vol>=1, RSI positive, gap_keep>=0.9.
    vcand=pd.concat(vall,ignore_index=True) if vall else pd.DataFrame()
    if len(vcand):
        vcand=vra.add_pullback_reaccel(vcand,vfeatures)
        vcand=vmp.add_preservation(vcand,vfeatures)
        q=vcand[(vcand.stop_dist_pct<=STOP_CAP)&vcand.reaccel_pass&
                (pd.to_numeric(vcand.volume_accel,errors='coerce')>=VOL_MIN)&
                vcand.rsi_positive_all&
                (pd.to_numeric(vcand.gap_keep_ratio,errors='coerce')>=GAP_KEEP_MIN)].copy()
        q['day']=pd.to_datetime(q.time).dt.date
        q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
        v_keys=q[['symbol','time']].copy(); v_keys['source']='V_REBOUND'
    else:
        v_keys=pd.DataFrame(columns=['symbol','time','source'])

    v20_keys=keys_from_events(ev20,'V20')
    allk=pd.concat([v20_keys,slow_keys[['source','symbol','time']],v_keys[['source','symbol','time']]],ignore_index=True)
    allk['time']=pd.to_datetime(allk.time)
    allk=allk.sort_values(['symbol','time','source']).reset_index(drop=True)

    # Exact same symbol/time collisions.
    exact=(allk.groupby(['symbol','time']).source.agg(lambda x:'+'.join(sorted(set(x)))).reset_index(name='sources'))
    exact=exact[exact.sources.str.contains('\+')].copy()
    exact['kind']='EXACT'
    exact['minutes_apart']=0.0

    # Near collisions: different source on same symbol within 5 minutes.
    near=[]
    for sym,g in allk.groupby('symbol'):
        a=g.sort_values('time').reset_index(drop=True)
        for i in range(len(a)):
            for j in range(i+1,len(a)):
                dt=(a.time.iloc[j]-a.time.iloc[i]).total_seconds()/60.0
                if dt>5: break
                if a.source.iloc[i]==a.source.iloc[j]: continue
                near.append(dict(kind='NEAR_5M',symbol=sym,time=a.time.iloc[i],
                                 sources=f"{a.source.iloc[i]}->{a.source.iloc[j]}",minutes_apart=dt))
    near=pd.DataFrame(near)
    out=pd.concat([exact[['kind','symbol','time','sources','minutes_apart']],near],ignore_index=True) if len(near) else exact[['kind','symbol','time','sources','minutes_apart']]
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    out.to_csv(OUT,index=False)

    print('\n=== ENGINE5 PRE-INTEGRATION SOURCE CONFLICTS ===')
    print(f'V20 signals={len(v20_keys)} | SLOW_TURN selected={len(slow_keys)} | V_REBOUND selected={len(v_keys)}')
    print(f'EXACT collisions={len(exact)} | NEAR<=5m collisions={len(near)}')
    print('\n=== EXACT ===')
    print(exact[['symbol','time','sources']].to_string(index=False) if len(exact) else 'NONE')
    print('\n=== NEAR <=5M ===')
    print(near[['symbol','time','sources','minutes_apart']].to_string(index=False) if len(near) else 'NONE')
    print('\nIntegration rule target:')
    print('- Preserve source per entry. Never infer V_REBOUND from (time,symbol) metadata existence.')
    print('- If exact conflicts exist, resolve by explicit source priority/state, not dict overwrite order.')
    print('- If no exact conflicts exist, still source-tag now so future/OOS overlap cannot corrupt exits.')
    print('WROTE',OUT)

if __name__=='__main__': main()
