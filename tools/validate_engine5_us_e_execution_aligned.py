from __future__ import annotations

"""Validate Engine5 E-series with causally aligned execution prices.

Purpose
-------
The historical Engine5 entry tuple stores the completed 5m bar close under the
5m completion timestamp.  For a 09:30..09:34 bar stamped 09:35 this makes the
entry tuple price equal to the previous 1m close, while the signal timestamp is
09:35.

E-series rule in this validator:
- keep signal generation exactly as validate_engine5_us_e_all_versions
- keep native USD / original ET
- when an entry signal is stamped at t, execute at the raw 1m OPEN at t
- never use future 1m close at t
- preserve the 5m-derived risk geometry (band width / score / flags)
- KR engines remain untouched

This is a validation wrapper, not a production live-engine edit.
"""

import pickle
from pathlib import Path
import pandas as pd
import numpy as np

import tools.validate_engine5_us_e_all_versions as e
import tools.validate_engine5_v17c_multi_symbol as multi

ROOT = Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE = ROOT / 'us_e_core.pkl'
OUT = ROOT / 'us_e_execution_alignment_summary.csv'


def n(x):
    return str(x).zfill(6)


def raw_open_lookup(raw):
    out = {}
    for sym, bars in raw.items():
        z = bars[['time','open']].copy()
        z['time'] = pd.to_datetime(z['time'])
        out[n(sym)] = dict(zip(z['time'], pd.to_numeric(z['open'], errors='coerce')))
    return out


def align_entry_stream(ev, opens):
    """Replace only tuple entry price with raw 1m open at stamped signal time."""
    out = {}
    missing = 0
    changed = 0
    deltas = []
    for ts, rows in ev.items():
        t = pd.Timestamp(ts)
        nr = []
        for c in rows:
            x = list(c)
            sym = n(x[0])
            op = opens.get(sym, {}).get(t, np.nan)
            if not np.isfinite(op):
                missing += 1
                # Cannot execute causally at t without a raw bar. Drop the entry.
                continue
            old = float(x[1])
            x[1] = float(op)
            nr.append(tuple(x))
            changed += 1
            if old:
                deltas.append((float(op) / old - 1.0) * 100.0)
        if nr:
            out[t] = nr
    return out, dict(
        aligned=changed,
        missing=missing,
        median_open_vs_old_pct=float(np.median(deltas)) if deltas else np.nan,
        p95_abs_open_vs_old_pct=float(np.percentile(np.abs(deltas),95)) if deltas else np.nan,
        max_abs_open_vs_old_pct=float(np.max(np.abs(deltas))) if deltas else np.nan,
    )


def gross_stat(label, trades, signals, diag):
    p = pd.to_numeric(trades.pnl_pct, errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    gp = float(p[p>0].sum()) if len(p) else 0.0
    gl = float(-p[p<0].sum()) if len(p) else 0.0
    return dict(
        variant=label,
        signals=signals,
        trades=len(p),
        wins=int((p>0).sum()),
        win_pct=float((p>0).mean()*100.0) if len(p) else 0.0,
        gross_sum_pct=float(p.sum()) if len(p) else 0.0,
        gross_pf=(gp/gl if gl>0 else np.inf),
        **diag,
    )


def main():
    if not CORE.exists():
        raise FileNotFoundError(CORE)
    with CORE.open('rb') as fh:
        d = pickle.load(fh)
    if d.get('cache_schema') != 'US_E_USD_ET_V1' or d.get('price_unit') != 'USD' or d.get('fx_applied') is not False:
        raise RuntimeError('E cache must be native USD / original ET / no FX')

    e.apply_us_session_clock()
    raw = d['raw']; cfg = d['cfg']; packed = d['packed']; states = d['states']; scored = d['scored']; micros = d['micros']
    opens = raw_open_lookup(raw)

    raw_entries = e.v8.pack_entry_events(scored)
    ev10 = e.sweep.filt_open(raw_entries)
    ev16, waits = e.v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = e.v17b.build_v17b(ev16, scored, waits)
    ev18, _ = e.h.build_veto_stream(ev17, micros)

    rows = []
    print('=== E EXECUTION ALIGNMENT VALIDATION ===')
    print('Signal timestamp unchanged. Entry execution = raw 1m OPEN at that timestamp.')
    print('No performance tuning; this checks the impact of correcting stale entry prices.')

    for label, ev in [('V17CE',ev17),('V18E',ev18)]:
        aligned, diag = align_entry_stream(e.upgrade(ev), opens)
        tr = multi.simulate_multi(packed, aligned, states, e.THRESHOLD)
        r = gross_stat(label + '_ALIGNED_OPEN', tr, e.count_events(aligned), diag)
        rows.append(r)
        print(f"{label}: aligned={diag['aligned']} missing={diag['missing']} trades={r['trades']} "
              f"open-old median={diag['median_open_vs_old_pct']:+.6f}% "
              f"p95abs={diag['p95_abs_open_vs_old_pct']:.6f}% maxabs={diag['max_abs_open_vs_old_pct']:.6f}%")

    pd.DataFrame(rows).to_csv(OUT,index=False)
    print('WROTE',OUT)
    print('VALIDATION ONLY. KR engines unchanged. E live/production execution not modified.')


if __name__ == '__main__':
    main()
