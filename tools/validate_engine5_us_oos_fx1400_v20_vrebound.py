from __future__ import annotations

"""US OOS V20/V-rebound validation after simple USD->KRW unit conversion.

The frozen KR gates RAW52 (V20) and RAW30 (V-rebound) are absolute MACD-gap
change units. US cached prices are USD, so this script converts ONLY those
absolute linear MACD quantities to KRW-equivalent units with FX=1400.

Equivalent interpretation: if the entire US OHLC series were multiplied by
1400 before MACD calculation, MACD/gap/gap_delta would be multiplied by 1400,
while RSI, signs, relative strength, percentages and price structure remain
unchanged.

No strategy threshold is changed. No US tuning.
"""

import pickle
from pathlib import Path
import pandas as pd

import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_integrated_full_history as integ

CACHE=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE/'us_engine5_core.pkl'
PERSIST=CACHE/'slow_turn_persistence_candidates.csv'
OUT=CACHE/'us_oos_fx1400_v20_vrebound_summary.csv'
TRADES=CACHE/'us_oos_fx1400_v20_vrebound_trades.csv'
SIGNALS=CACHE/'us_oos_fx1400_v20_vrebound_signals.csv'
FX=1400.0
NY='America/New_York'


def n(x): return str(x).zfill(6)


def stat_row(label,tags,packed,states):
    tr=integ.simulate(packed,states,tags)
    s=integ.stat(label,tr)
    return tr,dict(variant=label,signals=len(tags),**{k:s[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})


def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    print('=== US OOS FX1400 RAW-GATE VALIDATION ===',flush=True)
    print('USD absolute MACD quantities -> KRW equivalent by x1400.',flush=True)
    print('RAW52 / RAW30 thresholds unchanged. No OOS tuning.',flush=True)

    # V20: raw MACD gap acceleration is linear in price units. Baseline scales
    # identically, so REL is unchanged.
    strength_fx={}
    for sym,f in strength.items():
        q=f.copy()
        if 'macd_strength_raw' in q:q['macd_strength_raw']=pd.to_numeric(q['macd_strength_raw'],errors='coerce')*FX
        if 'macd_strength_baseline' in q:q['macd_strength_baseline']=pd.to_numeric(q['macd_strength_baseline'],errors='coerce')*FX
        strength_fx[n(sym)]=q

    legacy,pf=uscache.build_base_candidates_fast(raw,cfg,scored,micros,completed)
    pf_fx={}
    for sym,f in pf.items():
        q=f.copy()
        # V-rebound RAW30 is applied to gap_delta. Other uses are sign or ratios,
        # both invariant to positive scaling.
        if 'gap_delta' in q:q['gap_delta']=pd.to_numeric(q['gap_delta'],errors='coerce')*FX
        if 'macd_slope' in q:q['macd_slope']=pd.to_numeric(q['macd_slope'],errors='coerce')*FX
        pf_fx[n(sym)]=q

    old_persist=integ.PERSIST_SRC; old_rec=integ.sri.reconstruct_base_candidates; old_vload=integ.vold.load_cache; old_read=integ.pd.read_csv
    integ.PERSIST_SRC=PERSIST
    integ.sri.reconstruct_base_candidates=lambda *_: legacy.copy()
    integ.vold.load_cache=lambda sym,*_:(pf_fx[n(sym)],micros[n(sym)])
    def rd(path,*a,**k):
        x=old_read(path,*a,**k)
        try:same=Path(path)==PERSIST
        except TypeError:same=False
        if same and 'entry_time' in x:x['entry_time']=pd.to_datetime(x.entry_time,utc=True).dt.tz_convert(NY)
        return x
    integ.pd.read_csv=rd
    try:
        src=integ.build_sources(raw,cfg,scored,strength_fx,completed,micros)
    finally:
        integ.PERSIST_SRC=old_persist; integ.sri.reconstruct_base_candidates=old_rec; integ.vold.load_cache=old_vload; integ.pd.read_csv=old_read

    v20=[x for x in src if x['source']=='V20']
    vr=[x for x in src if x['source']=='V_REBOUND']
    print(f'V20 SIGNALS={len(v20)} | V_REBOUND SIGNALS={len(vr)}',flush=True)

    rows=[]; parts=[]; sig=[]
    for label,tags in [('V20_FX1400',v20),('V_REBOUND_FX1400',vr)]:
        tr,s=stat_row(label,tags,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;parts.append(q)
        sig.extend(dict(variant=label,symbol=x['symbol'],time=x['time'],source=x['source']) for x in tags)
        print(f"{label}: signals={s['signals']} trades={s['trades']} wins={s['wins']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.6f}% avg={s['avg_net_pct']:+.6f}% PF={s['pf']:.3f} maxloss={s['max_loss_pct']}",flush=True)

    pd.DataFrame(rows).to_csv(OUT,index=False)
    (pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()).to_csv(TRADES,index=False)
    pd.DataFrame(sig).to_csv(SIGNALS,index=False)
    print('WROTE',OUT);print('WROTE',TRADES);print('WROTE',SIGNALS)

if __name__=='__main__':main()
