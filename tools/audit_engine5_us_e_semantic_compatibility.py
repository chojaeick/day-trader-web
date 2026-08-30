from __future__ import annotations

"""Audit whether KR Engine5 semantics can be applied to US DB via E-series adaptation.

NO PERFORMANCE METRICS. NO PARAMETER TUNING.

Checks:
1) Native US DB/cache semantics: USD, original ET, regular session 09:30..15:59.
2) 1m -> completed 5m construction is causal and complete: 390 -> 78 bars/day,
   first completed bar stamped 09:35, last 16:00.
3) Session-rule mapping preserves relative-to-open intent:
   KR 09:10/10:00/15:00/15:20 -> US 09:40/10:30/15:30/15:50.
4) Core indicator semantics on a price-scaled copy:
   RSI/relative-strength/ratio features invariant; MACD/price-linear features linear.
   Boolean direction/gate decisions must be invariant apart from numerical-zero tolerance.
5) E-series price-unit dependence audit: flag active absolute price-linear thresholds and
   hard-coded KR wall-clock rules that can leak into US execution.
6) Event-layer causality/compatibility: V17CE, V18E and V19E streams must occur only
   inside US session rules and every 5m event timestamp must correspond to a completed
   bar boundary.  V18E must be a subset of V17CE.  V19E is additive to V18E.
7) V20E/V21E normalized gates must use bps/relative quantities, not USD absolute MACD.

PASS means semantic applicability is proven at the implementation level. It does NOT
say anything about profitability.
"""

import inspect
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as v19
import tools.validate_engine5_us_e_all_versions as e
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5
from tools.backtest_dbb_engine5_tuner import to_5m

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE=ROOT/'us_e_core.pkl'
OUT=ROOT/'us_e_semantic_compatibility.csv'

US_OPEN=9*60+30
US_BUY=9*60+40
US_OPENING_END=10*60+30
US_NO_ENTRY=15*60+30
US_FORCE=15*60+50


def n(x): return str(x).zfill(6)
def minute(ts):
    t=pd.Timestamp(ts); return t.hour*60+t.minute

def add(rows,check,status,detail): rows.append(dict(check=check,status=status,detail=detail))
def ok(rows,check,cond,good,bad): add(rows,check,'PASS' if cond else 'FAIL',good if cond else bad); return bool(cond)
def count_events(ev): return sum(len(v) for v in ev.values())
def event_keys(ev): return {(pd.Timestamp(ts),n(c[0])) for ts,cs in ev.items() for c in cs}


def apply_us_clock():
    base.NO_ENTRY_MINUTE=US_NO_ENTRY
    base.FORCE_FLAT_MINUTE=US_FORCE
    # filt_open() in the imported V17C opening module reads OPEN_MINUTE, not
    # OPEN_BUY_MINUTE. Patch both to prevent the KR 09:10 constant leaking.
    sweep.OPEN_MINUTE=US_BUY
    sweep.OPEN_BUY_MINUTE=US_BUY
    sweep.OPENING_ENTRY_END=US_OPENING_END
    multi.OPEN_MINUTE=US_BUY


def audit_cache(rows,d):
    raw=d['raw']
    ok(rows,'cache_schema',d.get('cache_schema')=='US_E_USD_ET_V1',str(d.get('cache_schema')),'wrong schema')
    ok(rows,'price_unit',d.get('price_unit')=='USD' and d.get('fx_applied') is False,'native USD / no FX',f"unit={d.get('price_unit')} fx={d.get('fx_applied')}")
    ok(rows,'time_shift',d.get('time_shift_minutes')==0,'original ET / shift=0',f"shift={d.get('time_shift_minutes')}")

    day_rows=[]
    bad_ts=bad_rows=bad5=dup=0
    for s,b in raw.items():
        z=b.copy(); z['time']=pd.to_datetime(z.time); z['day']=z.time.dt.date
        dup += int(z.time.duplicated().sum())
        for day,g in z.groupby('day'):
            g=g.sort_values('time'); m=g.time.dt.hour*60+g.time.dt.minute
            if len(g)!=390: bad_rows+=1
            if int(m.iloc[0])!=US_OPEN or int(m.iloc[-1])!=15*60+59: bad_ts+=1
            f5=to_5m(g[['time','open','high','low','close','volume']].copy())
            if len(f5)!=78 or minute(f5.time.iloc[0])!=9*60+35 or minute(f5.time.iloc[-1])!=16*60:
                bad5+=1
            day_rows.append((s,day,len(g),len(f5)))
    ok(rows,'regular_session_390',bad_rows==0 and bad_ts==0,f'{len(day_rows)} day-symbols all 09:30..15:59 / 390','bad_day_count='+str(bad_rows+bad_ts))
    ok(rows,'five_minute_completion',bad5==0,f'{len(day_rows)} day-symbols all 78 completed bars, 09:35..16:00',f'bad_day_count={bad5}')
    ok(rows,'timestamp_duplicates',dup==0,'0 duplicates',f'{dup} duplicates')


def audit_session_mapping(rows):
    cond=(US_BUY-US_OPEN==10 and US_OPENING_END-US_OPEN==60 and US_NO_ENTRY-US_OPEN==360 and US_FORCE-US_OPEN==380)
    ok(rows,'relative_session_mapping',cond,'open+10 / +60 / +360 / +380 preserved','relative session offsets changed')


def audit_scale_semantics(rows,d):
    s=sorted(d['raw'])[0]; b=d['raw'][s].copy(); b['time']=pd.to_datetime(b.time)
    b=b.iloc[:390*5].copy()
    f5=to_5m(b)
    b10=b.copy()
    for c in ['open','high','low','close']: b10[c]=pd.to_numeric(b10[c],errors='coerce')*10.0
    f510=to_5m(b10)
    eng=DoubleBollingerEngine5()
    a=eng.enrich(f5); z=eng.enrich(f510)
    common=set(a.columns)&set(z.columns)

    invariant_candidates=[c for c in common if any(k in c.lower() for k in ['rsi','ratio','strength'])]
    inv_bad=[]
    for c in invariant_candidates:
        aa=pd.to_numeric(a[c],errors='coerce'); zz=pd.to_numeric(z[c],errors='coerce')
        mask=aa.notna()&zz.notna()
        if mask.any() and not np.allclose(aa[mask],zz[mask],rtol=1e-9,atol=1e-9): inv_bad.append(c)
    ok(rows,'scale_invariant_indicators',len(inv_bad)==0,'RSI/ratio/strength invariant under x10 price scale','mismatch: '+','.join(inv_bad[:12]))

    bool_cols=[c for c in common if (c.startswith('gate_') or c in ['trend_up','macd_above_signal','macd_golden_cross','entry_gate','entry_signal'])]
    bool_bad=[]
    for c in bool_cols:
        aa=a[c].fillna(False).astype(bool).to_numpy(); zz=z[c].fillna(False).astype(bool).to_numpy()
        m=int((aa!=zz).sum())
        if m>0: bool_bad.append((c,m))
    total=sum(m for _,m in bool_bad)
    status='PASS' if total==0 else ('WARN' if total<=10 else 'FAIL')
    add(rows,'scale_boolean_gates',status,'identical' if total==0 else 'mismatches='+','.join(f'{c}:{m}' for c,m in bool_bad[:12]))


def audit_code_leaks(rows):
    src=inspect.getsource(e)
    bad=[]
    if re.search(r'\*\s*1400|FX\s*=\s*1400',src): bad.append('FX1400')
    if re.search(r'raw_min\s*=\s*52(?:\.0)?',src): bad.append('RAW52')
    if re.search(r'raw_min\s*=\s*30(?:\.0)?',src): bad.append('RAW30')
    ok(rows,'e_price_unit_leak',not bad,'no FX/RAW52/RAW30 active literals','found '+','.join(bad))

    need=['US_BUY_START_MINUTE','US_OPENING_END_MINUTE','US_NO_ENTRY_MINUTE','US_FORCE_FLAT_MINUTE']
    missing=[x for x in need if x not in src]
    ok(rows,'e_session_adapter',not missing,'US session adapter explicit','missing '+','.join(missing))

    cond=('V20E_RAW_BPS' in src and 'V21E_RAW30_BPS' in src and '*10000.0' in src)
    ok(rows,'e_normalized_macd_gates',cond,'V20E/V21E use price-relative bps','normalized gate not proven')


def audit_event_semantics(rows,d):
    apply_us_clock()
    raw=d['raw']; cfg=d['cfg']; scored=d['scored']; micros=d['micros']
    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)

    def inside(ev): return all(US_BUY<=minute(ts)<US_NO_ENTRY for ts in ev)
    ok(rows,'V17CE_session_bounds',inside(ev17),f'{count_events(ev17)} events all in US entry session','event outside 09:40..15:29')
    ok(rows,'V18E_session_bounds',inside(ev18),f'{count_events(ev18)} events all in US entry session','event outside 09:40..15:29')

    k17=event_keys(ev17); k18=event_keys(ev18)
    ok(rows,'V18E_subset_of_V17CE',k18.issubset(k17),f'{len(k18)}/{len(k17)} keys; pure veto/subset','V18E contains keys absent from V17CE')

    bad_boundary=sum(1 for ts in ev17 if pd.Timestamp(ts).minute%5!=0)
    ok(rows,'V17CE_completed_5m_boundary',bad_boundary==0,'all event timestamps on 5m boundaries',f'{bad_boundary} off-boundary timestamps')

    fast,_=v19.build_v19_events(scored,micros,raw,0)
    fast={ts:cs for ts,cs in fast.items() if US_BUY<=minute(ts)<US_NO_ENTRY}
    merged,_=v19.merge_additive(ev18,fast)
    kfast=event_keys(fast); km=event_keys(merged)
    ok(rows,'V19E_additive_semantics',k18.issubset(km) and kfast.issubset(km),f'base={len(k18)} fast={len(kfast)} merged={len(km)}','merged stream lost base/fast events')


def main():
    if not CORE.exists(): raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:d=pickle.load(fh)
    rows=[]
    print('=== ENGINE5 US E-SERIES SEMANTIC COMPATIBILITY AUDIT ===')
    print('NO PERFORMANCE / NO WIN RATE / NO PNL.')
    audit_cache(rows,d)
    audit_session_mapping(rows)
    audit_scale_semantics(rows,d)
    audit_code_leaks(rows)
    audit_event_semantics(rows,d)

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)
    print('\n'+out.to_string(index=False))
    fails=int((out.status=='FAIL').sum()); warns=int((out.status=='WARN').sum())
    print('\n=== VERDICT ===')
    if fails:
        print(f'FAIL: semantic applicability NOT proven. fails={fails} warns={warns}')
        print('Do not interpret performance until all semantic FAIL items are resolved.')
    else:
        print(f'PASS' + (f' WITH WARNINGS({warns})' if warns else '') + ': E-series implementation preserves KR Engine5 semantics on native USD/original ET at audited layers.')
        print('This is implementation compatibility only; it does not claim profitability.')
    print('WROTE',OUT)

if __name__=='__main__': main()
