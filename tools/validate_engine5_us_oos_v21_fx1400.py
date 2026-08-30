from __future__ import annotations

"""US OOS validation for official V21.

V21 = V20 + latest Slow-turn re-arm/guarded-DEEP + V-rebound.
For US only, absolute MACD quantities are converted from USD units to
KRW-equivalent units with FX=1400 before applying the unchanged KR RAW gates:
- V20 RAW52 / REL1.45
- V-rebound RAW30

Slow-turn structure and all other thresholds are unchanged. No US tuning.
"""

import pickle
from collections import Counter
from pathlib import Path
import pandas as pd

import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised

CACHE=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE/'us_engine5_core.pkl'
PERSIST=CACHE/'slow_turn_persistence_candidates.csv'
OUT=CACHE/'us_oos_v21_fx1400_summary.csv'
TRADES=CACHE/'us_oos_v21_fx1400_trades.csv'
SIGNALS=CACHE/'us_oos_v21_fx1400_signals.csv'
CUTS=(-0.15,-0.20,-0.30,-0.50)
FX=1400.0
NY='America/New_York'

def n(x): return str(x).zfill(6)
def counts(tags): return Counter(x['source'] for x in tags)

def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    print('=== US OOS V21 FX1400 ===',flush=True)
    print('V21 = V20 + latest Slow-turn + V-rebound.',flush=True)
    print('Only absolute MACD quantities are x1400; KR thresholds unchanged. No OOS tuning.',flush=True)

    # V20 absolute MACD strength -> KRW-equivalent units.
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
        if 'gap_delta' in q:q['gap_delta']=pd.to_numeric(q['gap_delta'],errors='coerce')*FX
        if 'macd_slope' in q:q['macd_slope']=pd.to_numeric(q['macd_slope'],errors='coerce')*FX
        pf_fx[n(sym)]=q

    old_persist=integ.PERSIST_SRC; old_rec=integ.sri.reconstruct_base_candidates; old_vload=integ.vold.load_cache; old_read=integ.pd.read_csv; old_rev=revised.st.load_or_build_cache
    integ.PERSIST_SRC=PERSIST
    integ.sri.reconstruct_base_candidates=lambda *_:legacy.copy()
    integ.vold.load_cache=lambda sym,*_:(pf_fx[n(sym)],micros[n(sym)])
    # Slow-turn must remain on the original parity-checked USD feature values.
    revised.st.load_or_build_cache=lambda sym,*_:(pf[n(sym)],micros[n(sym)])
    def rd(path,*a,**k):
        x=old_read(path,*a,**k)
        try:same=Path(path)==PERSIST
        except TypeError:same=False
        if same and 'entry_time' in x:x['entry_time']=pd.to_datetime(x.entry_time,utc=True).dt.tz_convert(NY)
        return x
    integ.pd.read_csv=rd

    try:
        current=integ.build_sources(raw,cfg,scored,strength_fx,completed,micros)
        v20=[x for x in current if x['source']=='V20']
        vr=[x for x in current if x['source']=='V_REBOUND']
        print(f'V20 SIGNALS={len(v20)} | V_REBOUND SIGNALS={len(vr)}',flush=True)
        print('BUILD LATEST SLOW-TURN...',flush=True)
        allslow=revised.build_all_slow(raw,cfg,completed,micros)
        print(f'ALL RE-ARMED READY+1M CANDIDATES={len(allslow)}',flush=True)

        rows=[]; parts=[]; sigparts=[]
        for cut in CUTS:
            slow=revised.slow_tags(revised.select_revised(allslow,cut))
            tags=sorted(v20+vr+slow,key=lambda z:(pd.Timestamp(z['time']),z['symbol'],z['source']))
            tr=integ.simulate(packed,states,tags)
            st=integ.stat(f'V21_{cut}',tr); c=counts(tags)
            row=dict(variant='V21',cut=cut,signals=len(tags),v20_signals=c.get('V20',0),slow_signals=c.get('SLOW_TURN',0),v_rebound_signals=c.get('V_REBOUND',0),**{k:st[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})
            rows.append(row)
            q=tr.copy();q['cut']=cut;parts.append(q)
            sigparts.append(pd.DataFrame([dict(cut=cut,source=x['source'],symbol=x['symbol'],time=x['time']) for x in tags]))
            print(f"V21 cut={cut}: signals={row['signals']} trades={row['trades']} wins={row['wins']} win={row['win_pct']:.2f}% net={row['net_sum_pct']:+.6f}% avg={row['avg_net_pct']:+.6f}% PF={row['pf']:.3f} maxloss={row['max_loss_pct']}",flush=True)
            print(f"  source signals: V20={row['v20_signals']} SLOW={row['slow_signals']} V_REBOUND={row['v_rebound_signals']}",flush=True)

        pd.DataFrame(rows).to_csv(OUT,index=False)
        pd.concat(parts,ignore_index=True).to_csv(TRADES,index=False)
        pd.concat(sigparts,ignore_index=True).to_csv(SIGNALS,index=False)
        print('WROTE',OUT);print('WROTE',TRADES);print('WROTE',SIGNALS)
    finally:
        integ.PERSIST_SRC=old_persist; integ.sri.reconstruct_base_candidates=old_rec; integ.vold.load_cache=old_vload; integ.pd.read_csv=old_read; revised.st.load_or_build_cache=old_rev

if __name__=='__main__':main()
