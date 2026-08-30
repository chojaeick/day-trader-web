from __future__ import annotations

"""Compare KR V17C and US V17CE MACD-context semantics.

No performance metrics. This asks whether both markets admit the same structural case:
MACD below signal (negative oscillator) but the negative gap is improving toward zero.
"""

from dataclasses import replace
import pickle
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
import tools.validate_engine5_us_e_all_versions as e
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

US_CORE=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/us_e_core.pkl')


def n(x): return str(x).zfill(6)
def minute(ts):
    t=pd.Timestamp(ts); return t.hour*60+t.minute

def clip_us(ev):
    return {pd.Timestamp(ts):cs for ts,cs in ev.items() if e.US_BUY_START_MINUTE<=minute(ts)<e.US_NO_ENTRY_MINUTE}

def keys(ev): return {(pd.Timestamp(ts),n(c[0])) for ts,cs in ev.items() for c in cs}

def num(v):
    try:
        x=float(v); return x if np.isfinite(x) else np.nan
    except Exception:return np.nan

def row_for(frame,ts):
    q=frame[frame.time<=pd.Timestamp(ts)]
    return None if q.empty else q.iloc[-1]

def classify(scored,ev,label):
    rows=[]
    for ts,cs in ev.items():
        for c in cs:
            sym=n(c[0]); f=scored[sym]; r=row_for(f,ts)
            if r is None: continue
            gap=num(r.get('macd_gap'))
            macd=num(r.get('macd')); sig=num(r.get('macd_signal'))
            if not np.isfinite(gap) and np.isfinite(macd) and np.isfinite(sig): gap=macd-sig
            prev=row_for(f,pd.Timestamp(ts)-pd.Timedelta(minutes=5))
            pg=num(prev.get('macd_gap')) if prev is not None else np.nan
            if prev is not None and not np.isfinite(pg):
                pm=num(prev.get('macd')); ps=num(prev.get('macd_signal'))
                if np.isfinite(pm) and np.isfinite(ps): pg=pm-ps
            gd=gap-pg if np.isfinite(gap) and np.isfinite(pg) else np.nan
            rows.append(dict(market=label,symbol=sym,time=pd.Timestamp(ts),macd_gap=gap,macd_gap_prev=pg,gap_delta=gd,
                             below=bool(np.isfinite(gap) and gap<0),improving=bool(np.isfinite(gd) and gd>0),
                             gate_macd_context=bool(r.get('gate_macd_context',False)),
                             gate_macd_accel=bool(r.get('gate_macd_accel',False)),
                             gate_macd_rising=bool(r.get('gate_macd_rising',False)),
                             gate_rsi_rising=bool(r.get('gate_rsi_rising',False)),
                             gate_rsi_persistent=bool(r.get('gate_rsi_persistent',False)),
                             gate_trend_up=bool(r.get('gate_trend_up',False)),
                             entry_gate=bool(r.get('entry_gate',False))))
    return pd.DataFrame(rows)

def build_kr():
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames=base.build_cfg_frames(raw,cfg); f10={n(s):v10._refine_entry_frame(f) for s,f in frames.items()}; scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    ev10=sweep.filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    return scored,ev17

def build_us():
    with US_CORE.open('rb') as fh:d=pickle.load(fh)
    e.apply_us_session_clock(); raw=d['raw']; cfg=d['cfg']; scored=d['scored']; micros=d['micros']
    ev10=clip_us(sweep.filt_open(v8.pack_entry_events(scored))); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev17=clip_us(ev17)
    return scored,ev17

def summary(df,label):
    total=len(df); below=int(df.below.sum()); bi=int((df.below & df.improving).sum())
    print(f'{label}: events={total} below_signal={below} ({below/total*100 if total else 0:.2f}%) below+improving={bi} ({bi/total*100 if total else 0:.2f}%)')
    if total:
        q=df[df.below & df.improving]
        if len(q):
            print('  gates among below+improving:',
                  'context',f'{q.gate_macd_context.mean()*100:.1f}%',
                  'accel',f'{q.gate_macd_accel.mean()*100:.1f}%',
                  'rising',f'{q.gate_macd_rising.mean()*100:.1f}%',
                  'rsi_rising',f'{q.gate_rsi_rising.mean()*100:.1f}%',
                  'trend_up',f'{q.gate_trend_up.mean()*100:.1f}%')

def main():
    print('=== KR V17C vs US V17CE MACD CONTEXT SEMANTICS ===')
    print('NO PERFORMANCE / NO PNL')
    ks,ke=build_kr(); us,ue=build_us()
    k=classify(ks,ke,'KR'); u=classify(us,ue,'US')
    summary(k,'KR V17C'); summary(u,'US V17CE')
    out=pd.concat([k,u],ignore_index=True)
    p=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/kr_us_macd_context_semantics.csv')
    out.to_csv(p,index=False)
    print('\n=== FIRST 10 KR BELOW+IMPROVING ===')
    print(k[k.below & k.improving].head(10)[['symbol','time','macd_gap','gap_delta','gate_macd_context','gate_macd_accel','gate_macd_rising','gate_rsi_rising','gate_trend_up','entry_gate']].to_string(index=False))
    print('\n=== FIRST 10 US BELOW+IMPROVING ===')
    print(u[u.below & u.improving].head(10)[['symbol','time','macd_gap','gap_delta','gate_macd_context','gate_macd_accel','gate_macd_rising','gate_rsi_rising','gate_trend_up','entry_gate']].to_string(index=False))
    print('\nWROTE',p)

if __name__=='__main__': main()
