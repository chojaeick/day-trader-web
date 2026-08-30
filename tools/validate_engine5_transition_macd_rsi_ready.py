from __future__ import annotations

"""Validate a transition READY path that does not wait for trend_up.

Goal from manual review:
- When 5m DBB mid slope is still negative / trend_up is False,
- but MACD gap is improving and RSI is rising together,
- mark a transition READY state,
- then require actual 1m price confirmation before considering an entry.

This is diagnostic only. It does NOT modify V20 or production logic.
The key target is 950160 on 2026-08-14 around the chart's 10:55 bar
(engine completed-bar label is expected around 11:00).
"""

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

TARGET='950160'
DAY=pd.Timestamp('2026-08-14').date()
OUT=Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation/transition_macd_rsi_ready.csv')


def n(x): return str(x).zfill(6)
def num(s): return pd.to_numeric(s,errors='coerce')

def mono(vals):
    s=pd.Series(vals,dtype='float64').dropna()
    if len(s)<2:return np.nan
    d=s.diff().dropna(); up=float(d[d>0].sum()) if (d>0).any() else 0.; dn=float(-d[d<0].sum()) if (d<0).any() else 0.
    return up/(up+dn) if up+dn>0 else np.nan

def price_confirm(m,ts):
    # Causal 1m confirmation using only minutes up to the 5m completed-bar timestamp.
    q=m[(m.time<=ts)&(m.time>=ts-pd.Timedelta(minutes=5))].copy()
    if len(q)<3:return dict(px_progress=np.nan,px_mono=np.nan,close_slope=np.nan,confirm=False)
    c=num(q.close).dropna()
    if len(c)<3 or c.iloc[0]<=0:return dict(px_progress=np.nan,px_mono=np.nan,close_slope=np.nan,confirm=False)
    prog=float(c.iloc[-1]/c.iloc[0]-1.)*100.
    pm=mono(c.values)
    slope=float(c.iloc[-1]-c.iloc[0])
    # No tuned threshold here: actual price must be net higher and path must be more up than down.
    ok=bool(prog>0 and np.isfinite(pm) and pm>=0.50)
    return dict(px_progress=prog,px_mono=pm,close_slope=slope,confirm=ok)


def main():
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    micros={n(s):h.build_micro(b,cfg) for s,b in raw.items()}

    rows=[]
    for sym,f in scored.items():
        z=f.copy().sort_values('time').reset_index(drop=True)
        z['time']=pd.to_datetime(z.time)
        macd_delta=num(z.macd_gap_delta)
        rsi_slope=num(z.rsi_slope)
        # Transition READY: do not require trend_up or positive mid slope.
        # Require synchronized present-tense improvement, not one indicator alone.
        ready=(~z.trend_up.fillna(False)) & (macd_delta>0) & (rsi_slope>0)
        for _,r in z[ready].iterrows():
            ts=pd.Timestamp(r.time)
            pc=price_confirm(micros[sym],ts)
            rows.append(dict(
                symbol=sym,time=ts,close=float(r.close),mid_slope8=float(r.mid_slope8) if pd.notna(r.mid_slope8) else np.nan,
                trend_up=bool(r.trend_up),macd_gap=float(r.macd_gap) if pd.notna(r.macd_gap) else np.nan,
                macd_gap_delta=float(r.macd_gap_delta) if pd.notna(r.macd_gap_delta) else np.nan,
                rsi=float(r.rsi) if pd.notna(r.rsi) else np.nan,rsi_slope=float(r.rsi_slope) if pd.notna(r.rsi_slope) else np.nan,
                golden=bool(r.macd_golden_cross) if pd.notna(r.macd_golden_cross) else False,
                px_progress_1m_pct=pc['px_progress'],px_mono_1m=pc['px_mono'],price_confirm=pc['confirm'],
                transition_entry=bool(pc['confirm'])
            ))

    out=pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(OUT,index=False)

    tgt=out[(out.symbol==TARGET)&(out.time.dt.date==DAY)] if len(out) else out
    print('=== MACD + RSI TRANSITION READY (trend_up not required) ===')
    print(f'ALL_READY={len(out)} | ALL_PRICE_CONFIRMED={int(out.transition_entry.sum()) if len(out) else 0}')
    print('\nTARGET 950160 2026-08-14 10:40-11:20')
    if len(tgt):
        q=tgt[(tgt.time>=pd.Timestamp('2026-08-14 10:40:00+09:00'))&(tgt.time<=pd.Timestamp('2026-08-14 11:20:00+09:00'))].copy()
        if len(q):
            q['time']=q.time.dt.strftime('%H:%M')
            print(q[['time','close','mid_slope8','macd_gap_delta','rsi','rsi_slope','golden','px_progress_1m_pct','px_mono_1m','price_confirm','transition_entry']].to_string(index=False))
        else: print('NONE')
    else: print('NONE')

    if len(tgt):
        e=tgt[tgt.transition_entry]
        print('\nTARGET_FIRST_CONFIRMED=',e.time.min().strftime('%H:%M') if len(e) else 'NONE')
    print('DETAIL_CSV',OUT)

if __name__=='__main__': main()
