from __future__ import annotations

import pickle
from pathlib import Path
import pandas as pd

import tools.validate_engine5_integrated_full_history as integ

CACHE_DIR=Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE=CACHE_DIR/'us_engine5_core.pkl'
PERSIST=CACHE_DIR/'slow_turn_persistence_candidates.csv'
OUT_TRADES=CACHE_DIR/'us_oos_integrated_trades.csv'
OUT_SUMMARY=CACHE_DIR/'us_oos_integrated_summary.csv'
OUT_SIGNALS=CACHE_DIR/'us_oos_integrated_signals.csv'


def main():
    if not CORE.exists():raise FileNotFoundError(f'{CORE} missing; run build_engine5_us_oos_cache first')
    if not PERSIST.exists():raise FileNotFoundError(PERSIST)
    with CORE.open('rb') as fh:d=pickle.load(fh)
    raw=d['raw']; cfg=d['cfg']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; completed=d['completed']; micros=d['micros']
    old=integ.PERSIST_SRC; integ.PERSIST_SRC=PERSIST
    try: tagged=integ.build_sources(raw,cfg,scored,strength,completed,micros)
    finally: integ.PERSIST_SRC=old
    tr=integ.simulate(packed,states,tagged)
    sm=pd.DataFrame([integ.stat('US_OOS_INTEGRATED',tr)])
    sig=pd.DataFrame([{k:x.get(k) for k in ['source','symbol','time']} for x in tagged])
    tr.to_csv(OUT_TRADES,index=False); sm.to_csv(OUT_SUMMARY,index=False); sig.to_csv(OUT_SIGNALS,index=False)
    print('\n=== US OOS INTEGRATED SUMMARY ===')
    print(sm.to_string(index=False))
    print('\n=== TRADES BY SOURCE ===')
    if len(tr):
        for src,g in tr.groupby('source'):
            s=integ.stat(src,g); print(f"{src}: n={s['trades']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.6f}% PF={s['pf']:.3f} max_loss={s['max_loss_pct']:+.6f}%")
    print('\n=== TRADES BY SYMBOL ===')
    if len(tr):
        for sym,g in tr.groupby('symbol'):
            s=integ.stat(sym,g); print(f"{sym}: n={s['trades']} net={s['net_sum_pct']:+.6f}% PF={s['pf']:.3f}")
    print('\nWROTE',OUT_TRADES); print('WROTE',OUT_SUMMARY); print('WROTE',OUT_SIGNALS)
    print('IMPORTANT: thresholds are unchanged from the KR integrated checkpoint. Do not tune from this OOS result.')
if __name__=='__main__':main()
