from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
BASE = OUT_DIR / 'v20_current_39_cases.csv'
INTEG = OUT_DIR / 'integrated_full_history_trades.csv'
SIGNALS = OUT_DIR / 'integrated_full_history_signals.csv'
OUT = OUT_DIR / 'integrated_v20_reconciliation.csv'

FEE_RT_PCT = 0.25
EXT_CAP = 8.0


def n(x): return str(x).zfill(6)

def main():
    for p in [BASE, INTEG, SIGNALS]:
        if not p.exists():
            raise FileNotFoundError(p)

    b = pd.read_csv(BASE)
    b['symbol'] = b['symbol'].astype(str).str.zfill(6)
    b['entry_time'] = pd.to_datetime(b['entry_time'])
    if 'net_pct' not in b.columns:
        if 'gross_pct' in b.columns:
            b['net_pct'] = pd.to_numeric(b['gross_pct'], errors='coerce') - FEE_RT_PCT
        elif 'pnl_pct' in b.columns:
            b['net_pct'] = pd.to_numeric(b['pnl_pct'], errors='coerce') - FEE_RT_PCT

    it = pd.read_csv(INTEG)
    it['symbol'] = it['symbol'].astype(str).str.zfill(6)
    it['entry_time'] = pd.to_datetime(it['entry_time'])
    iv20 = it[it['source'].eq('V20')].copy()

    sig = pd.read_csv(SIGNALS)
    sig['symbol'] = sig['symbol'].astype(str).str.zfill(6)
    sig['time'] = pd.to_datetime(sig['time'])

    # Exact V20 trade preservation check.
    base_keys = set(zip(b.symbol, b.entry_time))
    integ_keys = set(zip(iv20.symbol, iv20.entry_time))
    missing = b[[ 'symbol','entry_time','net_pct'] + ([ 'reason' ] if 'reason' in b.columns else [])].copy()
    missing = missing[~missing.apply(lambda r: (r.symbol, r.entry_time) in integ_keys, axis=1)].copy()

    rows=[]
    for _,r in missing.sort_values('entry_time').iterrows():
        sym=n(r.symbol); ts=pd.Timestamp(r.entry_time)
        # Was the original V20 signal removed by extension guard?
        qv = sig[(sig.source=='V20') & (sig.symbol==sym) & (sig.time==ts)]
        if qv.empty:
            cause='V20_SIGNAL_FILTERED_BEFORE_INTEGRATION'
        else:
            # If signal survived, another source may have opened the same symbol earlier and still owned it.
            earlier = it[(it.symbol==sym) & (it.entry_time < ts) & (it.exit_time.astype(str).notna())].copy()
            if len(earlier):
                earlier['exit_time']=pd.to_datetime(earlier['exit_time'])
                active=earlier[earlier.exit_time > ts].sort_values('entry_time')
            else:
                active=pd.DataFrame()
            if len(active):
                a=active.iloc[-1]
                cause=f"BLOCKED_BY_OPEN_{a.source}"
                owner_entry=pd.Timestamp(a.entry_time)
                owner_exit=pd.Timestamp(a.exit_time)
            else:
                cause='SIGNAL_PRESENT_BUT_NO_MATCHING_TRADE'
                owner_entry=pd.NaT; owner_exit=pd.NaT
        if 'owner_entry' not in locals(): owner_entry=pd.NaT; owner_exit=pd.NaT
        rows.append(dict(symbol=sym,entry_time=ts,baseline_net_pct=float(r.net_pct),
                         baseline_reason=r.get('reason',''),cause=cause,
                         owner_entry_time=owner_entry,owner_exit_time=owner_exit))
        owner_entry=pd.NaT; owner_exit=pd.NaT

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)

    # V20 signals that exist but do not become trades (includes later duplicate/occupied cases beyond baseline keys).
    sigv=sig[sig.source.eq('V20')][['symbol','time']].drop_duplicates().copy()
    sigv['became_trade']=sigv.apply(lambda r:(r.symbol,r.time) in integ_keys,axis=1)
    skipped=sigv[~sigv.became_trade].copy()

    print('\n=== V20 BASELINE -> INTEGRATED RECONCILIATION ===')
    print(f'BASELINE_TRADES={len(b)} | INTEGRATED_V20_TRADES={len(iv20)} | MISSING_BASELINE_TRADES={len(out)}')
    print(f'BASELINE_NET={pd.to_numeric(b.net_pct,errors="coerce").sum():+.6f}%')
    print(f'MISSING_BASELINE_NET={pd.to_numeric(out.baseline_net_pct,errors="coerce").sum():+.6f}%' if len(out) else 'MISSING_BASELINE_NET=+0.000000%')

    print('\n=== MISSING BASELINE V20 TRADES ===')
    if len(out):
        cols=['symbol','entry_time','baseline_net_pct','baseline_reason','cause','owner_entry_time','owner_exit_time']
        print(out[cols].to_string(index=False))
    else:
        print('NONE')

    print('\n=== CAUSE COUNTS ===')
    print(out.groupby('cause').agg(n=('symbol','size'),net=('baseline_net_pct','sum')).to_string() if len(out) else 'NONE')

    print('\n=== INTEGRATED V20 SIGNALS THAT DID NOT BECOME V20 TRADES ===')
    print(skipped.to_string(index=False) if len(skipped) else 'NONE')

    print('\nReading target:')
    print('- Expected: two large late/top V20 losses may be absent because the extreme-extension guard filtered them.')
    print('- Any surviving V20 signal blocked by an already-open SLOW_TURN or V_REBOUND position should be explicit.')
    print('- No source should mutate an already-open position. Reconciliation should explain every missing baseline trade.')
    print('WROTE',OUT)

if __name__=='__main__':
    main()
