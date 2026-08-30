from __future__ import annotations

"""Trace where out-of-US-session entry events first survive in the E-series pipeline.

NO PERFORMANCE METRICS. NO PNL.

Pipeline audited:
raw scored entry events -> sweep.filt_open -> v16 -> v17b -> v18 veto.
The goal is to identify the exact stage and timestamp responsible for events outside
US entry session 09:40..15:29 ET.
"""

import pickle
from pathlib import Path
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi

CORE=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/us_e_core.pkl')
OUT=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache/us_e_session_leak_stage.csv')
US_BUY=9*60+40
US_NO_ENTRY=15*60+30
US_OPENING_END=10*60+30
US_FORCE=15*60+50


def minute(ts):
    t=pd.Timestamp(ts); return t.hour*60+t.minute

def n(x): return str(x).zfill(6)
def event_count(ev): return sum(len(v) for v in ev.values())

def leak_rows(stage,ev):
    rows=[]
    for ts,cs in sorted(ev.items(),key=lambda z:pd.Timestamp(z[0])):
        m=minute(ts)
        if US_BUY<=m<US_NO_ENTRY: continue
        for c in cs:
            rows.append(dict(stage=stage,time=pd.Timestamp(ts),minute=m,symbol=n(c[0]),event_width=len(c)))
    return rows

def summarize(stage,ev):
    leaks=leak_rows(stage,ev)
    if leaks:
        q=pd.DataFrame(leaks)
        early=int((q.minute<US_BUY).sum()); late=int((q.minute>=US_NO_ENTRY).sum())
        print(f'{stage}: events={event_count(ev)} LEAKS={len(q)} early={early} late={late} first={q.time.min()} last={q.time.max()}')
    else:
        print(f'{stage}: events={event_count(ev)} LEAKS=0')
    return leaks


def main():
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:d=pickle.load(fh)

    # Patch every known E-session knob, then print what each module actually holds.
    base.NO_ENTRY_MINUTE=US_NO_ENTRY
    base.FORCE_FLAT_MINUTE=US_FORCE
    sweep.OPEN_MINUTE=US_BUY
    if hasattr(sweep,'OPEN_BUY_MINUTE'): sweep.OPEN_BUY_MINUTE=US_BUY
    if hasattr(sweep,'OPENING_ENTRY_END'): sweep.OPENING_ENTRY_END=US_OPENING_END
    multi.OPEN_MINUTE=US_BUY

    print('=== E SESSION LEAK STAGE DIAGNOSTIC ===')
    print('NO PERFORMANCE / NO PNL')
    print('expected entry window: 09:40..15:29 ET')
    print('runtime constants:')
    print(' base.NO_ENTRY_MINUTE=',getattr(base,'NO_ENTRY_MINUTE',None))
    print(' base.FORCE_FLAT_MINUTE=',getattr(base,'FORCE_FLAT_MINUTE',None))
    print(' sweep.OPEN_MINUTE=',getattr(sweep,'OPEN_MINUTE',None))
    print(' sweep.OPEN_BUY_MINUTE=',getattr(sweep,'OPEN_BUY_MINUTE',None))
    print(' sweep.OPENING_ENTRY_END=',getattr(sweep,'OPENING_ENTRY_END',None))
    print(' multi.OPEN_MINUTE=',getattr(multi,'OPEN_MINUTE',None))

    raw=d['raw']; cfg=d['cfg']; scored=d['scored']; micros=d['micros']
    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)

    allrows=[]
    for stage,ev in [('RAW_ENTRY',raw_entries),('AFTER_FILT_OPEN',ev10),('AFTER_V16',ev16),('AFTER_V17B',ev17),('AFTER_V18',ev18)]:
        allrows.extend(summarize(stage,ev))

    if allrows:
        q=pd.DataFrame(allrows).sort_values(['time','stage','symbol'])
        q.to_csv(OUT,index=False)
        print('\n=== FIRST 40 LEAKS ===')
        print(q.head(40).to_string(index=False))
        print('\n=== LEAK COUNT BY STAGE / HH:MM ===')
        q['hhmm']=pd.to_datetime(q.time).dt.strftime('%H:%M')
        print(q.groupby(['stage','hhmm']).size().reset_index(name='count').head(80).to_string(index=False))
        print('\nWROTE',OUT)
    else:
        print('\nNO SESSION LEAKS FOUND.')

if __name__=='__main__': main()
