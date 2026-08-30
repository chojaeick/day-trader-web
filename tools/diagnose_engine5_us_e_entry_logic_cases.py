from __future__ import annotations

"""Representative-case semantic diagnosis for US E-series entries.

No performance metrics. This inspects whether the E-series makes entry decisions for the
same reasons intended by the KR Engine5 logic.

Primary target: SPY 2026-02-02 12:00 ET.
Also prints a compact sample of other V17CE entries where MACD is below signal.
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
import tools.validate_engine5_us_e_all_versions as e

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE=ROOT/'us_e_core.pkl'
OUT=ROOT/'us_e_entry_logic_cases.csv'
TARGET_SYM='000SPY'
TARGET_TS=pd.Timestamp('2026-02-02 12:00:00-05:00')


def n(x): return str(x).zfill(6)
def minute(ts):
    t=pd.Timestamp(ts); return t.hour*60+t.minute

def clip(ev):
    return {pd.Timestamp(ts):cs for ts,cs in ev.items() if e.US_BUY_START_MINUTE<=minute(ts)<e.US_NO_ENTRY_MINUTE}
def ev_keys(ev): return {(pd.Timestamp(ts),n(c[0])) for ts,cs in ev.items() for c in cs}
def num(v):
    try:
        x=float(v); return x if np.isfinite(x) else np.nan
    except Exception:return np.nan


def row_for(frame,ts):
    q=frame[frame.time<=pd.Timestamp(ts)]
    return None if q.empty else q.iloc[-1]


def same_ts(a,b):
    try:
        return pd.Timestamp(a).value == pd.Timestamp(b).value
    except Exception:
        return False


def build_record(sym,ts,scored,strength,k17,k18):
    f=scored[sym]
    r=row_for(f,ts)
    if r is None:return None
    sr=row_for(strength[sym],ts)
    gap=num(r.get('macd_gap'))
    macd=num(r.get('macd'))
    sig=num(r.get('macd_signal'))
    if not np.isfinite(gap) and np.isfinite(macd) and np.isfinite(sig): gap=macd-sig
    prev=row_for(f,pd.Timestamp(ts)-pd.Timedelta(minutes=5))
    gap_prev=num(prev.get('macd_gap')) if prev is not None else np.nan
    if prev is not None and not np.isfinite(gap_prev):
        pm=num(prev.get('macd')); ps=num(prev.get('macd_signal'))
        if np.isfinite(pm) and np.isfinite(ps): gap_prev=pm-ps
    gap_delta=gap-gap_prev if np.isfinite(gap) and np.isfinite(gap_prev) else np.nan
    rec=dict(symbol=sym,time=pd.Timestamp(ts),close=num(r.get('close')),
             macd=macd,macd_signal=sig,macd_gap=gap,macd_gap_prev=gap_prev,macd_gap_delta=gap_delta,
             macd_below_signal=bool(np.isfinite(gap) and gap<0),
             macd_gap_improving=bool(np.isfinite(gap_delta) and gap_delta>0),
             rsi=num(r.get('rsi')),rsi_slope=num(r.get('rsi_slope')),trend_up=bool(r.get('trend_up',False)),
             entry_score=num(r.get('entry_score')),entry_gate=bool(r.get('entry_gate',False)),
             v17ce=((pd.Timestamp(ts),sym) in k17),v18e=((pd.Timestamp(ts),sym) in k18))
    for c in sorted([c for c in f.columns if c.startswith('gate_')]):
        rec[c]=bool(r.get(c,False))
    for c in ['macd_above_signal','macd_golden_cross','macd_gap_improving','rsi_accelerating','outer_expanding','inner_traverse_up']:
        if c in f.columns: rec[c]=bool(r.get(c,False))
    if sr is not None:
        raw=num(sr.get('macd_strength_raw')); close=num(sr.get('close'))
        rec['v20e_raw_bps']=raw/close*10000.0 if np.isfinite(raw) and np.isfinite(close) and close!=0 else np.nan
        rec['v20e_rel']=num(sr.get('macd_strength_rel'))
    return rec


def main():
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:d=pickle.load(fh)
    e.apply_us_session_clock()
    raw=d['raw']; cfg=d['cfg']; scored=d['scored']; strength=d['strength']; micros=d['micros']

    raw_entries=v8.pack_entry_events(scored)
    ev10=clip(sweep.filt_open(raw_entries))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev17=clip(ev17)
    ev18,_=h.build_veto_stream(ev17,micros); ev18=clip(ev18)
    k17=ev_keys(ev17); k18=ev_keys(ev18)

    rows=[]
    target=build_record(TARGET_SYM,TARGET_TS,scored,strength,k17,k18)
    if target is not None: rows.append(target)

    added=0
    for ts,cs in sorted(ev17.items()):
        for c in cs:
            sym=n(c[0])
            r=build_record(sym,ts,scored,strength,k17,k18)
            if r and r['macd_below_signal']:
                if not (sym==TARGET_SYM and same_ts(ts,TARGET_TS)): rows.append(r)
                added+=1
                if added>=15: break
        if added>=15: break

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)
    print('=== US E ENTRY LOGIC CASE DIAGNOSTIC ===')
    print('NO PERFORMANCE / NO PNL')
    print('\n=== TARGET: SPY 2026-02-02 12:00 ET ===')
    mask=(out.symbol==TARGET_SYM) & out['time'].map(lambda x:same_ts(x,TARGET_TS))
    tq=out[mask]
    if tq.empty: print('TARGET NOT FOUND')
    else:
        basecols=['symbol','time','close','macd','macd_signal','macd_gap','macd_gap_prev','macd_gap_delta','macd_below_signal','macd_gap_improving','rsi','rsi_slope','trend_up','entry_score','entry_gate','v17ce','v18e','v20e_raw_bps','v20e_rel']
        gates=[c for c in out.columns if c.startswith('gate_') or c in ['macd_above_signal','macd_golden_cross','rsi_accelerating','outer_expanding','inner_traverse_up']]
        cols=[c for c in basecols+gates if c in tq.columns]
        print(tq[cols].to_string(index=False))

    print('\n=== SAMPLE V17CE ENTRIES WITH MACD BELOW SIGNAL ===')
    q=out[out.macd_below_signal].head(15)
    cols=[c for c in ['symbol','time','macd_gap','macd_gap_prev','macd_gap_delta','macd_gap_improving','rsi','rsi_slope','trend_up','entry_gate','v17ce','v18e','v20e_raw_bps','v20e_rel'] if c in q.columns]
    print(q[cols].to_string(index=False) if len(q) else 'NONE')
    print('\nWROTE',OUT)

if __name__=='__main__': main()
