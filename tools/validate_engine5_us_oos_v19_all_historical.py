from __future__ import annotations

"""US OOS validation for the remaining historical V19 paths.

Reuses the existing US core cache. No data rebuild, no threshold changes, no
US tuning. Historical constructors are imported unchanged from the original KR
validators and evaluated through the same current integrated exit simulator.
"""

import pickle
from pathlib import Path
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_veto_plus_fast_trigger_additive_sweep as additive
import tools.validate_engine5_v19_momentum_birth_fast_trigger as birth
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as strict
import tools.validate_engine5_integrated_full_history as integ

CACHE=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE/'us_engine5_core.pkl'
OUT=CACHE/'us_oos_v19_all_historical_summary.csv'
TRADES=CACHE/'us_oos_v19_all_historical_trades.csv'
DIAG=CACHE/'us_oos_v19_all_historical_diag.csv'
DELAYS=(0,1,2,3)

def n(x): return str(x).zfill(6)
def flatten(src,ev):
    return [dict(source=src,symbol=n(c[0]),time=pd.Timestamp(ts),event=c,meta={}) for ts,cs in ev.items() for c in cs]
def count_events(ev): return sum(len(v) for v in ev.values())
def run(label,ev,packed,states):
    tags=flatten(label,ev)
    tr=integ.simulate(packed,states,tags)
    s=integ.stat(label,tr)
    return tr,dict(variant=label,signals=len(tags),**{k:s[k] for k in ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})

def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; micros=d['micros']
    print('=== US OOS REMAINING HISTORICAL V19 PATHS ===')
    print('NO CACHE REBUILD. NO THRESHOLD CHANGES. NO OOS TUNING.')

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)

    rows=[]; trade_parts=[]; diag_rows=[]

    # Include V16 because it was not printed by the previous all-version run.
    for label,ev in [('V16_WAIT_REACCEL',ev16),('V18_BASE',ev18)]:
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)

    # Historical pre-V19 additive fast-trigger sweep.
    hybrid,ready_diag=h.build_ready_trigger_stream(scored,micros)
    for delay in DELAYS:
        ev,added=additive.add_fast_events(ev18,hybrid,ready_diag,delay)
        label=f'FAST_ADDITIVE_D{delay}'
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)
        diag_rows.append(dict(variant=label,ready=len(ready_diag),triggered=count_events(ev)-count_events(ev18),raw_added=len(added)))

    # V19 momentum-birth implementation.
    for delay in DELAYS:
        fast,dg=birth.build_v19_birth_events(scored,micros,raw,delay)
        ev,added=birth.merge_additive(ev18,fast)
        label=f'V19_BIRTH_D{delay}'
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)
        diag_rows.append(dict(variant=label,ready=len(dg),triggered=int((dg.status=='TRIGGERED').sum()) if len(dg) else 0,raw_added=len(added)))

    # Later strict 5m-prebuy + 1m-confirm V19 revision.
    for delay in DELAYS:
        fast,dg=strict.build_v19_events(scored,micros,raw,delay)
        ev,added=strict.merge_additive(ev18,fast)
        label=f'V19_STRICT_D{delay}'
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)
        diag_rows.append(dict(variant=label,ready=len(dg),triggered=int((dg.status=='TRIGGERED').sum()) if len(dg) else 0,raw_added=len(added)))

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)
    (pd.concat(trade_parts,ignore_index=True) if trade_parts else pd.DataFrame()).to_csv(TRADES,index=False)
    pd.DataFrame(diag_rows).to_csv(DIAG,index=False)

    print('\n=== SUMMARY ===')
    for _,r in out.iterrows():
        print(f"{r.variant}: signals={int(r.signals)} trades={int(r.trades)} wins={int(r.wins)} win={r.win_pct:.2f}% net={r.net_sum_pct:+.6f}% avg={r.avg_net_pct:+.6f}% PF={r.pf:.3f} maxloss={r.max_loss_pct}")
    print('\n=== ADDITIVE DIAGNOSTIC ===')
    for r in diag_rows:
        print(f"{r['variant']}: ready={r['ready']} triggered={r['triggered']} raw_added={r['raw_added']}")
    print('WROTE',OUT);print('WROTE',TRADES);print('WROTE',DIAG)

if __name__=='__main__':main()
