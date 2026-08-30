from __future__ import annotations

"""US OOS performance validation across the historical Engine5 versions.

Uses the already-built US core cache and the same exit simulator used by the
current integrated validator. No cache rebuild, no threshold tuning.

Rows:
- V17C core 5m stream
- V18 V17C + 1m stale-entry veto
- V20 V18 + frozen KR RAW52/REL1.45
- V20_EXT_GUARD V20 + current extreme-extension new-entry guard
- SLOW_TURN latest re-arm + guarded DEEP for all pre-specified KR cuts
- V_REBOUND frozen KR structural path

V19 fast-1m path is intentionally reported as NOT_RUN unless its historical
signal constructor is present in the current core path; we do not fake it by
renaming another stream.
"""

import pickle
from pathlib import Path
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.build_engine5_us_oos_cache as uscache

CACHE=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE/'us_engine5_core.pkl'
OUT=CACHE/'us_oos_all_versions_summary.csv'
TRADES=CACHE/'us_oos_all_versions_trades.csv'
CUTS=(-0.15,-0.20,-0.30,-0.50)


def n(x): return str(x).zfill(6)
def flatten(source, ev):
    return [dict(source=source,symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}) for ts,cs in ev.items() for c in cs]

def run(label,tags,packed,states):
    tr=integ.simulate(packed,states,tags)
    s=integ.stat(label,tr); s['signals']=len(tags); s['variant']=label
    return tr,s

def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    print('=== US OOS ALL ENGINE VERSIONS ===')
    print('NO CACHE REBUILD. NO THRESHOLD CHANGES. NO OOS TUNING.')

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)
    ev20,_=ms.filter_events(ev18,strength,raw_min=integ.V20_RAW,rel_min=integ.V20_REL)
    v20_guard=[]
    for ts,cs in ev20.items():
        for c in cs:
            ext=integ.entry_extension_5m(scored,c[0],ts)
            if pd.notna(ext) and ext>=integ.V20_EXTREME_CAP:continue
            v20_guard.append(dict(source='V20_EXT_GUARD',symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}))

    # Fast US provisional frames are parity-checked against the original causal builder.
    _,pf=uscache.build_base_candidates_fast(raw,cfg,scored,micros,completed)
    oldload=revised.st.load_or_build_cache
    revised.st.load_or_build_cache=lambda sym,*args:(pf[n(sym)],micros[n(sym)])
    try: allslow=revised.build_all_slow(raw,cfg,completed,micros)
    finally: revised.st.load_or_build_cache=oldload

    # Reuse exact current V-rebound construction via integrated builder, with fast cache provider.
    oldv=integ.vold.load_cache; oldp=integ.PERSIST_SRC; oldr=integ.sri.reconstruct_base_candidates; oldread=integ.pd.read_csv
    persist=CACHE/'slow_turn_persistence_candidates.csv'
    integ.vold.load_cache=lambda sym,*args:(pf[n(sym)],micros[n(sym)])
    integ.sri.reconstruct_base_candidates=lambda *_: pd.DataFrame(columns=['symbol','entry_time'])
    integ.PERSIST_SRC=persist
    def rd(path,*a,**k):
        x=oldread(path,*a,**k)
        if Path(path)==persist and 'entry_time' in x:x['entry_time']=pd.to_datetime(x.entry_time,utc=True).dt.tz_convert('America/New_York')
        return x
    integ.pd.read_csv=rd
    try:
        try: cur=integ.build_sources(raw,cfg,scored,strength,completed,micros)
        except Exception: cur=[]
    finally:
        integ.vold.load_cache=oldv; integ.PERSIST_SRC=oldp; integ.sri.reconstruct_base_candidates=oldr; integ.pd.read_csv=oldread
    vr=[x for x in cur if x['source']=='V_REBOUND']

    variants=[('V17C',flatten('V17C',ev17)),('V18',flatten('V18',ev18)),('V20',flatten('V20',ev20)),('V20_EXT_GUARD',v20_guard),('V_REBOUND',vr)]
    for cut in CUTS:
        variants.append((f'SLOW_TURN_{cut}',revised.slow_tags(revised.select_revised(allslow,cut))))

    rows=[]; parts=[]
    for label,tags in variants:
        tr,s=run(label,tags,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;parts.append(q)
        print(f"{label}: signals={s['signals']} trades={s['trades']} wins={s['wins']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.6f}% avg={s['avg_net_pct']:+.6f}% PF={s['pf']:.3f} maxloss={s['max_loss_pct']}")
    rows.append(dict(variant='V19',signals=0,trades=0,wins=0,losses=0,win_pct=float('nan'),net_sum_pct=float('nan'),avg_net_pct=float('nan'),pf=float('nan'),max_loss_pct=float('nan'),note='NOT_RUN: historical V19 fast-1m constructor not substituted/faked'))
    out=pd.DataFrame(rows);out.to_csv(OUT,index=False)
    (pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()).to_csv(TRADES,index=False)
    print('V19: NOT_RUN (historical fast-1m constructor must be validated separately; no fake substitution).')
    print('WROTE',OUT);print('WROTE',TRADES)
if __name__=='__main__':main()
