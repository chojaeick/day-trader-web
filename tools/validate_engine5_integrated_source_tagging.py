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
import tools.validate_engine5_slow_turn_regime_integrated as ri
import tools.validate_engine5_slow_turn_structure_ablation as ab
import tools.validate_engine5_v21_v_rebound_structural_stop as vold
import tools.validate_engine5_v21_v_rebound_state_machine as vsm
import tools.validate_engine5_v21_v_rebound_reaccel as vra
import tools.validate_engine5_v21_v_rebound_momentum_preservation as vmp
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
PERSIST_SRC=OUT_DIR/'slow_turn_persistence_candidates.csv'
OUT=OUT_DIR/'integrated_source_tagging_check.csv'
RAW_MIN=30.0; LEG_MIN=2.0; STOP_CAP=2.0; VOL_MIN=1.0; GAP_KEEP_MIN=.9

def n(x): return str(x).zfill(6)
def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception: return np.nan

def slow_select(r):
    z=f(r.zero_cross_bars); p5=f(r.joint5_persistence); p1=f(r.joint1_persistence); px=f(r.price_progress_1m_pct); gd=f(r.gap_delta_5m); rs=f(r.rsi_slope_5m); ext=f(r.close_progress_6m_pct)
    if not np.isfinite(z): return False
    if z<=1.5: return bool(px>=.75 and ext<4.)
    if z<=8.: return bool(p5>=.60 and p1>=.60 and px>=1.)
    if z<=12.: return bool(gd>=30. and rs>=10. and px>=1.5)
    return False

def rows_from_ev(ev,source):
    out=[]
    for ts,cs in ev.items():
        for c in cs: out.append(dict(symbol=n(c[0]),time=pd.Timestamp(ts),source=source))
    return out

def main():
    if not PERSIST_SRC.exists(): raise FileNotFoundError(PERSIST_SRC)
    raw={n(k):v for k,v in load_data().items()}; base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg); f10={n(s):v10._refine_entry_frame(x) for s,x in frames0.items()}; scored={n(s):x for s,x in reweight(f10,cfg,0.).items()}; strength={s:ms.add_strength(x) for s,x in scored.items()}; completed={s:rt.add_completed_strength(x) for s,x in scored.items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={}; vfeatures={}; vall=[]
    for sym,bars in raw.items():
        pf,m=vold.load_cache(sym,bars,cfg,completed[sym]); micros[sym]=m; z=vsm.add_features(pf,m,bars).sort_values('time').reset_index(drop=True); vfeatures[sym]=z; c=vsm.state_candidates(sym,z,scored[sym],RAW_MIN,LEG_MIN)
        if len(c): vall.append(c)
    ev18,_=h.build_veto_stream(ev17,micros); ev20,_=ms.filter_events(ev18,strength,raw_min=52.,rel_min=1.45)
    rows=rows_from_ev(ev20,'V20')

    base_cand=ri.reconstruct_base_candidates(raw,cfg,scored,completed,micros); base_cand['symbol']=base_cand.symbol.astype(str).str.zfill(6); base_cand['entry_time']=pd.to_datetime(base_cand.entry_time)
    p=pd.read_csv(PERSIST_SRC); p['symbol']=p.symbol.astype(str).str.zfill(6); p['entry_time']=pd.to_datetime(p.entry_time); keep=['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    sx=base_cand.merge(p[keep],on=['symbol','entry_time'],how='inner',validate='one_to_one'); ext=[]
    for _,r in sx.iterrows(): ext.append(ab.metric_window(micros[n(r.symbol)],pd.Timestamp(r.entry_time)))
    sx=pd.concat([sx.reset_index(drop=True),pd.DataFrame(ext)],axis=1); sx=sx[[slow_select(r) for _,r in sx.iterrows()]].copy()
    rows += [dict(symbol=n(r.symbol),time=pd.Timestamp(r.entry_time),source='SLOW_TURN') for _,r in sx.iterrows()]

    vc=pd.concat(vall,ignore_index=True) if vall else pd.DataFrame()
    if len(vc):
        vc=vra.add_pullback_reaccel(vc,vfeatures); vc=vmp.add_preservation(vc,vfeatures); q=vc[(vc.stop_dist_pct<=STOP_CAP)&vc.reaccel_pass&(pd.to_numeric(vc.volume_accel,errors='coerce')>=VOL_MIN)&vc.rsi_positive_all&(pd.to_numeric(vc.gap_keep_ratio,errors='coerce')>=GAP_KEEP_MIN)].copy(); q['day']=pd.to_datetime(q.time).dt.date; q=q.sort_values('time').drop_duplicates(['symbol','day'],keep='first')
        rows += [dict(symbol=n(r.symbol),time=pd.Timestamp(r.time),source='V_REBOUND') for _,r in q.iterrows()]

    x=pd.DataFrame(rows).sort_values(['symbol','time','source']).reset_index(drop=True)
    # Explicit source identity key. This is the proposed integration contract.
    x['entry_key']=x.apply(lambda r:f"{r.symbol}|{pd.Timestamp(r.time).isoformat()}|{r.source}",axis=1)
    dup_source=int(x.entry_key.duplicated().sum())
    exact=x.groupby(['symbol','time']).source.nunique().reset_index(name='source_count'); exact=exact[exact.source_count>1]
    near=[]
    for sym,g in x.groupby('symbol'):
        a=g.sort_values('time').reset_index(drop=True)
        for i in range(len(a)):
            for j in range(i+1,len(a)):
                dt=(a.time.iloc[j]-a.time.iloc[i]).total_seconds()/60.;
                if dt>5: break
                if a.source.iloc[i]!=a.source.iloc[j]: near.append((sym,a.time.iloc[i],a.source.iloc[i],a.time.iloc[j],a.source.iloc[j],dt))
    pd.DataFrame(near,columns=['symbol','first_time','first_source','second_time','second_source','minutes_apart']).to_csv(OUT,index=False)
    print('\n=== INTEGRATED SOURCE TAGGING CHECK ===')
    print(x.groupby('source').size().to_string())
    print(f'EXACT_MULTI_SOURCE={len(exact)} | DUPLICATE_SOURCE_KEYS={dup_source} | NEAR_5M={len(near)}')
    print('\n=== NEAR <=5M ===')
    if near: print(pd.DataFrame(near,columns=['symbol','first_time','first_source','second_time','second_source','minutes_apart']).to_string(index=False))
    else: print('NONE')
    print('\nSOURCE OWNERSHIP CONTRACT:')
    print('- Every entry carries source explicitly: V20 / SLOW_TURN / V_REBOUND.')
    print('- Once a position is opened, later signals do not mutate its source or exit ownership.')
    print('- V_REBOUND owns Higher-Low defensive stop and RUN hold logic.')
    print('- V20 and SLOW_TURN use their own normal trend/exit path; V metadata cannot leak into them.')
    print('- Exact same-time conflicts, if they appear OOS later, must be resolved before entry creation, never by metadata overwrite.')
    print('WROTE',OUT)
if __name__=='__main__': main()
