from __future__ import annotations

"""Audit Engine5 E-series entry timestamp/price alignment.

No performance metrics.
Checks whether an entry event stamped at minute T carries the actual 1m market
price at T, or a stale completed-5m close from T-1.
"""

import pickle
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.backtest_dbb_engine5_fast_tuner_v4 as base

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE=ROOT/'us_e_core.pkl'
OUT=ROOT/'e_entry_timestamp_price_alignment.csv'
US_BUY_START=9*60+40
US_OPEN_END=10*60+30
US_NO_ENTRY=15*60+30
US_FORCE_FLAT=15*60+50


def n(x): return str(x).zfill(6)

def apply_clock():
    base.NO_ENTRY_MINUTE=US_NO_ENTRY
    base.FORCE_FLAT_MINUTE=US_FORCE_FLAT
    sweep.OPEN_BUY_MINUTE=US_BUY_START
    sweep.OPENING_ENTRY_END=US_OPEN_END
    multi.OPEN_MINUTE=US_BUY_START


def raw_price_map(raw):
    out={}
    for s,b in raw.items():
        q=b[['time','open','high','low','close']].copy()
        q['time']=pd.to_datetime(q.time)
        out[n(s)]=q.set_index('time')
    return out


def collect(label,ev,pm):
    rows=[]
    for ts,cs in sorted(ev.items()):
        ts=pd.Timestamp(ts)
        for c in cs:
            sym=n(c[0])
            event_px=float(c[1]) if len(c)>1 and pd.notna(c[1]) else np.nan
            r=pm.get(sym)
            if r is None or ts not in r.index:
                rows.append(dict(variant=label,symbol=sym,time=ts,event_price=event_px,current_1m_open=np.nan,current_1m_close=np.nan,prev_1m_close=np.nan,match_current_close=False,match_prev_close=False,missing_1m=True))
                continue
            cur=r.loc[ts]
            if isinstance(cur,pd.DataFrame): cur=cur.iloc[-1]
            idx=r.index.get_loc(ts)
            prev=np.nan
            if isinstance(idx,(int,np.integer)) and idx>0: prev=float(r.iloc[idx-1].close)
            cc=float(cur.close); co=float(cur.open)
            rows.append(dict(variant=label,symbol=sym,time=ts,event_price=event_px,current_1m_open=co,current_1m_close=cc,prev_1m_close=prev,
                             delta_vs_current_pct=((event_px/cc-1)*100 if np.isfinite(event_px) and cc else np.nan),
                             delta_vs_prev_pct=((event_px/prev-1)*100 if np.isfinite(event_px) and np.isfinite(prev) and prev else np.nan),
                             match_current_close=bool(np.isclose(event_px,cc,rtol=0,atol=1e-10)),
                             match_prev_close=bool(np.isfinite(prev) and np.isclose(event_px,prev,rtol=0,atol=1e-10)),missing_1m=False))
    return rows


def main():
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:d=pickle.load(fh)
    if d.get('price_unit')!='USD' or d.get('fx_applied') is not False or d.get('time_shift_minutes')!=0:
        raise RuntimeError('Not a valid native-USD/original-ET E cache')
    apply_clock()
    raw=d['raw']; scored=d['scored']; cfg=d['cfg']; micros=d['micros']
    pm=raw_price_map(raw)
    ev10=sweep.filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)
    rows=collect('V17CE',ev17,pm)+collect('V18E',ev18,pm)
    z=pd.DataFrame(rows)
    z.to_csv(OUT,index=False)
    print('=== E ENTRY TIMESTAMP / PRICE ALIGNMENT AUDIT ===')
    print('NO PERFORMANCE METRICS.')
    for label,g in z.groupby('variant'):
        valid=g[~g.missing_1m]
        print(f'{label}: events={len(g)} missing_1m={int(g.missing_1m.sum())} current_close_match={int(valid.match_current_close.sum())}/{len(valid)} prev_close_match={int(valid.match_prev_close.sum())}/{len(valid)}')
        if len(valid):
            a=pd.to_numeric(valid.delta_vs_current_pct,errors='coerce').abs()
            print(f'  |event-current1mclose| median={a.median():.6f}% p95={a.quantile(.95):.6f}% max={a.max():.6f}%')
    bad=z[(~z.missing_1m)&(~z.match_current_close)].copy()
    print('\n=== FIRST 20 MISALIGNED EVENTS ===')
    cols=['variant','symbol','time','event_price','current_1m_open','current_1m_close','prev_1m_close','delta_vs_current_pct','match_prev_close']
    print(bad[cols].head(20).to_string(index=False) if len(bad) else 'NONE')
    print('\nWROTE',OUT)
    print('INTERPRETATION: if event_price repeatedly equals prev_1m_close rather than current 1m price at the stamped timestamp, Engine5 is executing a completed-5m signal at a stale price.')

if __name__=='__main__': main()
