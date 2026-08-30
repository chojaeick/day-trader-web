from __future__ import annotations

"""V3 diagnostic: causal RSI-50 cross weighting through actual Slow-turn entry.

V2's base transition score ends at READY.  This audit keeps that score unchanged, but
extends the *same provisional RSI series* from its turn low through actual entry_time so
a fast READY->entry RSI-50 cross is not missed.  This is diagnostic only.
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.validate_engine5_integrated_slow_turn_transition_score_v2 as v2
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config

OUT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
DETAIL = OUT / 'slow_turn_transition_score_v2_detail.csv'


def num(x): return pd.to_numeric(x, errors='coerce')

def clip01(x):
    try: return float(np.clip(float(x), 0.0, 1.0))
    except Exception: return 0.0


def build_pf_by_symbol():
    raw={v2.n(k):v for k,v in v2.load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0=v2.base.build_cfg_frames(raw,cfg)
    f10={v2.n(s):v2.v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={v2.n(s):f for s,f in v2.reweight(f10,cfg,0.0).items()}
    completed={s:v2.rt.add_completed_strength(f) for s,f in scored.items()}
    out={}
    for s in raw:
        pf,_=v2.st.load_or_build_cache(s,raw[s],cfg,completed[s])
        q=pf.copy().sort_values('time'); q['time']=pd.to_datetime(q['time'])
        out[s]=q
    return out


def extended_rsi50(r, pf):
    start_t=pd.Timestamp(r['rsi_turn_start'])
    entry_t=pd.Timestamp(r['entry_time'])
    q=pf[(pf.time>=start_t)&(pf.time<=entry_t)].copy()
    if q.empty or 'rsi' not in q.columns:
        return dict(rsi50_crossed_entry=False,rsi50_bonus=0.0,rsi_entry_value=np.nan,
                    rsi50_cross_time=pd.NaT,rsi50_cross_minutes=np.nan,rsi_extended_rows=len(q))
    q=q[['time','rsi']].copy(); q['rsi']=num(q['rsi']); q=q.dropna()
    if q.empty:
        return dict(rsi50_crossed_entry=False,rsi50_bonus=0.0,rsi_entry_value=np.nan,
                    rsi50_cross_time=pd.NaT,rsi50_cross_minutes=np.nan,rsi_extended_rows=0)
    start=float(q.iloc[0].rsi); end=float(q.iloc[-1].rsi)
    prev=q['rsi'].shift(1)
    hits=q[(prev<50.0)&(q['rsi']>=50.0)]
    crossed=bool(len(hits)) and start<50.0
    if not crossed:
        return dict(rsi50_crossed_entry=False,rsi50_bonus=0.0,rsi_entry_value=end,
                    rsi50_cross_time=pd.NaT,rsi50_cross_minutes=np.nan,rsi_extended_rows=len(q))
    ct=pd.Timestamp(hits.iloc[0].time)
    mins=max((ct-start_t).total_seconds()/60.0,1.0)
    rise=max(50.0-start,0.0)
    # Diagnostic weighting: meaningful low-to-50 rise AND fast crossing are both required.
    move_strength=clip01(rise/15.0)
    speed_strength=clip01((rise/mins)/4.0)
    bonus=20.0*min(move_strength,speed_strength)
    return dict(rsi50_crossed_entry=True,rsi50_bonus=bonus,rsi_entry_value=end,
                rsi50_cross_time=ct,rsi50_cross_minutes=mins,rsi_extended_rows=len(q))


def main():
    if not DETAIL.exists(): raise FileNotFoundError(DETAIL)
    x=pd.read_csv(DETAIL)
    needed={'rsi_turn_start','transition_score','entry_time'}
    missing=sorted(needed-set(x.columns))
    if missing: raise SystemExit(f'MISSING_COLUMNS {missing}')
    x['symbol']=x.symbol.astype(str).str.zfill(6); x['entry_time']=pd.to_datetime(x.entry_time)
    pf_by_symbol=build_pf_by_symbol()
    ext=[]
    for _,r in x.iterrows(): ext.append(extended_rsi50(r,pf_by_symbol[str(r.symbol).zfill(6)]))
    x=pd.concat([x.reset_index(drop=True),pd.DataFrame(ext)],axis=1)
    x['transition_score_v3']=num(x.transition_score)+num(x.rsi50_bonus)

    print('=== SLOW-TURN V3 RSI-50 THROUGH ACTUAL ENTRY ===')
    targets=[('058610','2026-08-13 09:25:00+09:00','V_TURN_SUCCESS'),('122630','2026-08-20 13:06:00+09:00','GRADUAL_FAILURE'),('950160','2026-08-14 10:59:00+09:00','VALID_SLOW_SUCCESS')]
    for sym,t,label in targets:
        q=x[(x.symbol==sym)&(x.entry_time==pd.Timestamp(t))]
        print(f'\n[{label}]')
        if q.empty: print('NOT FOUND'); continue
        r=q.iloc[0]
        for c in ['transition_score','rsi_turn_start_value','rsi_turn_end_value','rsi_entry_value','rsi50_crossed_entry','rsi50_cross_time','rsi50_cross_minutes','rsi50_bonus','transition_score_v3','net_pct','result']:
            if c in q.columns: print(f'{c:24s} {r[c]}')
    out=OUT/'slow_turn_transition_score_v3_rsi50_detail.csv'; x.to_csv(out,index=False)
    print('\nWROTE',out)

if __name__=='__main__': main()
