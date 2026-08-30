from __future__ import annotations

"""Show representative successful/failed V21E trades with entry-state diagnostics.

Reads only the fresh SQLite/USD/ET artifacts produced by
remap_and_validate_engine5_v21e_fresh_from_us_db.py. No DB remap and no performance tuning.

Purpose: inspect whether winning and losing US trades were triggered by sensible Engine5
wave states, rather than attributing KR/US divergence to 'different market waves'.
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP_PKL = ROOT / 'v21e_fresh_map.pkl'
TRADES_CSV = ROOT / 'v21e_fresh_trades.csv'
OUT = ROOT / 'v21e_success_failure_cases.csv'
FEE_RT_PCT = 0.25


def n(x):
    return str(x).zfill(6)


def f(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def row_at(frame: pd.DataFrame | None, ts):
    if frame is None or frame.empty:
        return None
    t = pd.Timestamp(ts)
    q = frame[pd.to_datetime(frame.time) <= t]
    return None if q.empty else q.iloc[-1]


def getv(r, *names):
    if r is None:
        return np.nan
    for c in names:
        if c in r.index:
            x = f(r[c])
            if np.isfinite(x):
                return x
    return np.nan


def main():
    if not MAP_PKL.exists():
        raise FileNotFoundError(MAP_PKL)
    if not TRADES_CSV.exists():
        raise FileNotFoundError(TRADES_CSV)

    with MAP_PKL.open('rb') as fh:
        d = pickle.load(fh)
    if d.get('schema') != 'V21E_FRESH_SQLITE_USD_ET_V1':
        raise RuntimeError(f"unexpected schema: {d.get('schema')}")

    tr = pd.read_csv(TRADES_CSV)
    tr['symbol'] = tr.symbol.astype(str).map(n)
    tr['entry_time'] = pd.to_datetime(tr.entry_time)
    tr['exit_time'] = pd.to_datetime(tr.exit_time)
    tr['pnl_pct'] = pd.to_numeric(tr.pnl_pct, errors='coerce')
    tr['net025_pct'] = tr.pnl_pct - FEE_RT_PCT

    scored = d['scored']
    strength = d['strength']
    tags = d['tags']

    tagmap = {}
    for x in tags:
        k = (n(x['symbol']), pd.Timestamp(x['time']), str(x['source']))
        tagmap.setdefault(k, x)

    rows = []
    for _, t in tr.iterrows():
        sym = n(t.symbol)
        ts = pd.Timestamp(t.entry_time)
        src = str(t.source)
        sr = row_at(scored.get(sym), ts)
        st = row_at(strength.get(sym), ts)

        macd = getv(sr, 'macd')
        signal = getv(sr, 'macd_signal', 'signal')
        gap = getv(sr, 'macd_gap')
        if not np.isfinite(gap) and np.isfinite(macd) and np.isfinite(signal):
            gap = macd - signal
        prev = row_at(scored.get(sym), ts - pd.Timedelta(minutes=5))
        pgap = getv(prev, 'macd_gap')
        if not np.isfinite(pgap):
            pm = getv(prev, 'macd'); ps = getv(prev, 'macd_signal', 'signal')
            if np.isfinite(pm) and np.isfinite(ps):
                pgap = pm - ps
        gap_delta = gap - pgap if np.isfinite(gap) and np.isfinite(pgap) else np.nan

        close = getv(sr, 'close')
        raw_strength = getv(st, 'macd_strength_raw')
        raw_bps = raw_strength / close * 10000.0 if np.isfinite(raw_strength) and np.isfinite(close) and close else np.nan
        rel = getv(st, 'macd_strength_rel')
        rsi = getv(sr, 'rsi')
        rsi_slope = getv(sr, 'rsi_slope')
        trend_up = bool(sr.get('trend_up', False)) if sr is not None else False
        entry_score = getv(sr, 'entry_score')

        tag = tagmap.get((sym, ts, src), {})
        meta = tag.get('meta', {}) if isinstance(tag, dict) else {}

        rows.append(dict(
            source=src, symbol=sym,
            entry_time=ts, exit_time=pd.Timestamp(t.exit_time),
            entry_price=f(t.entry_price), exit_price=f(t.exit_price),
            gross_pct=f(t.pnl_pct), net025_pct=f(t.net025_pct), reason=str(t.reason),
            hold_min=(pd.Timestamp(t.exit_time)-ts).total_seconds()/60.0,
            close_5m=close, macd=macd, signal=signal, macd_gap=gap,
            macd_gap_prev=pgap, macd_gap_delta=gap_delta,
            macd_below_signal=bool(np.isfinite(gap) and gap < 0),
            gap_improving=bool(np.isfinite(gap_delta) and gap_delta > 0),
            rsi=rsi, rsi_slope=rsi_slope, trend_up=trend_up,
            entry_score=entry_score, strength_bps=raw_bps, strength_rel=rel,
            structural_stop=f(meta.get('structural_stop', np.nan)),
            slow_regime=str(meta.get('regime', '')),
            slow_norm_mid_slope_pct=f(meta.get('norm_mid_slope_pct', np.nan)),
        ))

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)

    print('=== V21E SUCCESS / FAILURE CASES ===')
    print('Fresh SQLite/USD/ET baseline only. Net = gross - 0.25% round-trip cost.')
    print('Shows 2 best and 2 worst realized trades per V21E internal path.\n')

    cols = [
        'source','symbol','entry_time','exit_time','gross_pct','net025_pct','reason','hold_min',
        'macd_gap','macd_gap_delta','macd_below_signal','gap_improving',
        'rsi','rsi_slope','trend_up','strength_bps','strength_rel','slow_regime'
    ]

    for src in ['V20E','SLOW_TURN_E','V_REBOUND_E']:
        q = out[out.source == src].copy()
        if q.empty:
            continue
        winners = q.sort_values('net025_pct', ascending=False).head(2)
        losers = q.sort_values('net025_pct', ascending=True).head(2)
        print(f'=== {src}: SUCCESS ===')
        print(winners[cols].to_string(index=False, float_format=lambda x:f'{x:.4f}'))
        print(f'\n=== {src}: FAILURE ===')
        print(losers[cols].to_string(index=False, float_format=lambda x:f'{x:.4f}'))
        print()

    print('=== QUICK FAILURE SHAPE COUNTS ===')
    for src in ['V20E','SLOW_TURN_E','V_REBOUND_E']:
        q = out[(out.source == src) & (out.net025_pct <= 0)]
        if q.empty:
            continue
        print(
            f"{src}: losses={len(q)} | below_signal={int(q.macd_below_signal.sum())}/{len(q)} "
            f"| gap_improving={int(q.gap_improving.sum())}/{len(q)} "
            f"| trend_up={int(q.trend_up.sum())}/{len(q)}"
        )

    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
