from __future__ import annotations

"""US OOS validation for the remaining historical V19 paths.

Reuses the existing US core cache. No data rebuild, no threshold changes, no
US tuning. Historical constructors are imported unchanged from the original KR
validators and evaluated with the historical multi-symbol simulator they were
built for, so 12-field historical events are not forced through the newer
13-field integrated simulator.
"""

import pickle
from pathlib import Path
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_veto_plus_fast_trigger_additive_sweep as additive
import tools.validate_engine5_v19_momentum_birth_fast_trigger as birth
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as strict

CACHE=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE/'us_engine5_core.pkl'
OUT=CACHE/'us_oos_v19_all_historical_summary.csv'
TRADES=CACHE/'us_oos_v19_all_historical_trades.csv'
DIAG=CACHE/'us_oos_v19_all_historical_diag.csv'
DELAYS=(0,1,2,3)
THRESHOLD=50

def count_events(ev): return sum(len(v) for v in ev.values())

def stat(label,tr,signals):
    p=pd.to_numeric(tr.pnl_pct,errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    gp=float(p[p>0].sum()) if len(p) else 0.0
    gl=float(-p[p<0].sum()) if len(p) else 0.0
    gross=float(p.sum()) if len(p) else 0.0
    net=gross-len(p)*0.25
    return dict(
        variant=label,signals=int(signals),trades=int(len(p)),wins=int((p>0).sum()),losses=int((p<=0).sum()),
        win_pct=float((p>0).mean()*100.0) if len(p) else 0.0,
        net_sum_pct=net,avg_net_pct=float(net/len(p)) if len(p) else 0.0,
        pf=(gp/gl if gl>0 else float('inf')),max_loss_pct=(float(p.min()) if len(p) else float('nan')))

def run(label,ev,packed,states):
    tr=multi.simulate_multi(packed,ev,states,THRESHOLD)
    return tr,stat(label,tr,count_events(ev))

def main():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; micros=d['micros']
    print('=== US OOS REMAINING HISTORICAL V19 PATHS ===')
    print('NO CACHE REBUILD. NO THRESHOLD CHANGES. NO OOS TUNING.')
    print('HISTORICAL 12-FIELD EVENTS USE THEIR ORIGINAL MULTI-SYMBOL SIMULATOR.')

    raw_entries=v8.pack_entry_events(scored)
    ev10=sweep.filt_open(raw_entries)
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    ev18,_=h.build_veto_stream(ev17,micros)

    rows=[]; trade_parts=[]; diag_rows=[]

    for label,ev in [('V16_WAIT_REACCEL',ev16),('V18_BASE',ev18)]:
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)

    hybrid,ready_diag=h.build_ready_trigger_stream(scored,micros)
    for delay in DELAYS:
        ev,added=additive.add_fast_events(ev18,hybrid,ready_diag,delay)
        label=f'FAST_ADDITIVE_D{delay}'
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)
        diag_rows.append(dict(variant=label,ready=len(ready_diag),triggered=count_events(ev)-count_events(ev18),raw_added=len(added)))

    for delay in DELAYS:
        fast,dg=birth.build_v19_birth_events(scored,micros,raw,delay)
        ev,added=birth.merge_additive(ev18,fast)
        label=f'V19_BIRTH_D{delay}'
        tr,s=run(label,ev,packed,states); rows.append(s)
        if len(tr):q=tr.copy();q['variant']=label;trade_parts.append(q)
        diag_rows.append(dict(variant=label,ready=len(dg),triggered=int((dg.status=='TRIGGERED').sum()) if len(dg) else 0,raw_added=len(added)))

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
