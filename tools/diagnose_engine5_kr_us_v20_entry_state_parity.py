from __future__ import annotations

"""Compare actual entry-time V20 state semantics between KR V20 and US V20E.

Purpose: find whether US V20E is entering after the established-uptrend state has already
collapsed. This is a semantic/execution diagnostic, not a retuning sweep.
"""

import pickle
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
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_us_e_all_versions as e
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

US_MAP=Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation/v21e_fresh_map.pkl')
OUT=Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation/kr_us_v20_entry_state_parity.csv')
FEE=.25


def n(x): return str(x).zfill(6)

def row_at(f,ts):
    q=f[pd.to_datetime(f.time)<=pd.Timestamp(ts)]
    return None if q.empty else q.iloc[-1]

def num(x):
    try:
        z=float(x); return z if np.isfinite(z) else np.nan
    except Exception:return np.nan

def annotate(market,tr,scored,strength):
    rows=[]
    for _,t in tr.iterrows():
        sym=n(t.symbol); ts=pd.Timestamp(t.entry_time)
        r=row_at(scored[sym],ts); s=row_at(strength[sym],ts)
        if r is None: continue
        gap=num(r.get('macd_gap'))
        if not np.isfinite(gap): gap=num(r.get('macd'))-num(r.get('macd_signal'))
        prev=row_at(scored[sym],ts-pd.Timedelta(minutes=5))
        pg=num(prev.get('macd_gap')) if prev is not None else np.nan
        if prev is not None and not np.isfinite(pg): pg=num(prev.get('macd'))-num(prev.get('macd_signal'))
        gd=gap-pg if np.isfinite(gap) and np.isfinite(pg) else np.nan
        raw=num(s.get('macd_strength_raw')) if s is not None else np.nan
        px=num(s.get('close')) if s is not None else np.nan
        rows.append(dict(
            market=market,symbol=sym,entry_time=ts,pnl_pct=num(t.pnl_pct),net_pct=num(t.pnl_pct)-FEE,
            win=bool(num(t.pnl_pct)-FEE>0),trend_up=bool(r.get('trend_up',False)),
            macd_gap=gap,macd_above=bool(np.isfinite(gap) and gap>0),gap_delta=gd,
            gap_improving=bool(np.isfinite(gd) and gd>0),rsi=num(r.get('rsi')),
            rsi_slope=num(r.get('rsi_slope')),strength_bps=(raw/px*10000 if np.isfinite(raw) and np.isfinite(px) and px else np.nan),
            strength_rel=num(s.get('macd_strength_rel')) if s is not None else np.nan,
            reason=str(t.get('reason','')),
        ))
    return pd.DataFrame(rows)


def build_kr():
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0); states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames=base.build_cfg_frames(raw,cfg); f10={n(s):v10._refine_entry_frame(f) for s,f in frames.items()}; scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}; micros={s:h.build_micro(raw[s],cfg) for s in raw}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=52.0,rel_min=1.45)
    tr=multi.simulate_multi(packed,ev20,states,50)
    return annotate('KR_V20',tr,scored,strength)


def build_us():
    with US_MAP.open('rb') as fh:d=pickle.load(fh)
    e.apply_us_session_clock()
    raw=d['raw']; scored=d['scored']; strength=d['strength']
    cfg0=DoubleBollingerEngine5Config(); packed=v8.base.pack_exit_events(raw,cfg0); states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    tags=[x for x in d['tags'] if x['source']=='V20E']
    import tools.validate_engine5_integrated_full_history as integ
    tr=integ.simulate(packed,states,tags)
    return annotate('US_V20E',tr,scored,strength)


def summarize(x):
    rows=[]
    for market,g in x.groupby('market'):
        for label,q in [('ALL',g),('WIN',g[g.win]),('LOSS',g[~g.win])]:
            rows.append(dict(market=market,group=label,trades=len(q),win_pct=float(q.win.mean()*100) if len(q) else np.nan,
                trend_up_pct=float(q.trend_up.mean()*100) if len(q) else np.nan,
                macd_above_pct=float(q.macd_above.mean()*100) if len(q) else np.nan,
                gap_improving_pct=float(q.gap_improving.mean()*100) if len(q) else np.nan,
                median_rsi=float(q.rsi.median()) if len(q) else np.nan,
                median_rsi_slope=float(q.rsi_slope.median()) if len(q) else np.nan,
                median_strength_bps=float(q.strength_bps.median()) if len(q) else np.nan,
                median_strength_rel=float(q.strength_rel.median()) if len(q) else np.nan,
                net_sum=float(q.net_pct.sum()) if len(q) else 0.0))
    return pd.DataFrame(rows)


def main():
    print('=== KR V20 vs US V20E ACTUAL ENTRY-STATE PARITY ===')
    print('Checks state at realized entry time. No retuning.\n')
    kr=build_kr(); us=build_us(); x=pd.concat([kr,us],ignore_index=True)
    s=summarize(x)
    print(s.to_string(index=False,float_format=lambda v:f'{v:.4f}'))
    print('\n=== US V20E LOSSES WITH trend_up=FALSE (first 12) ===')
    q=us[(~us.win)&(~us.trend_up)].sort_values('net_pct').head(12)
    cols=['symbol','entry_time','net_pct','macd_gap','gap_delta','rsi','rsi_slope','strength_bps','strength_rel','reason']
    print(q[cols].to_string(index=False,float_format=lambda v:f'{v:.4f}') if len(q) else 'NONE')
    print('\n=== KR V20 LOSSES WITH trend_up=FALSE (first 12) ===')
    q=kr[(~kr.win)&(~kr.trend_up)].sort_values('net_pct').head(12)
    print(q[cols].to_string(index=False,float_format=lambda v:f'{v:.4f}') if len(q) else 'NONE')
    OUT.parent.mkdir(parents=True,exist_ok=True); x.to_csv(OUT,index=False)
    print('\nWROTE',OUT)

if __name__=='__main__': main()
