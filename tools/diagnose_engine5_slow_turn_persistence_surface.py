from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.validate_engine5_slow_turn_prototype as slow
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_zero_cross_candidates.csv'
OUT_DETAIL = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = OUT_DIR / 'slow_turn_persistence_summary.csv'


def n(x):
    return str(x).zfill(6)


def num(x):
    return pd.to_numeric(x, errors='coerce')


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def seq_monotonicity(vals):
    s = pd.Series(vals, dtype='float64').dropna()
    if len(s) < 2:
        return np.nan
    d = s.diff().dropna()
    up = float(d[d > 0].sum()) if (d > 0).any() else 0.0
    dn = float(-d[d < 0].sum()) if (d < 0).any() else 0.0
    return up / (up + dn) if (up + dn) > 0 else np.nan


def persistence_bucket(v):
    if not np.isfinite(v):
        return 'NA'
    if v >= 0.80:
        return 'HIGH>=0.80'
    if v >= 0.60:
        return 'MID_0.60-0.80'
    return 'LOW<0.60'


def distance_bucket(v):
    if not np.isfinite(v):
        return 'NA'
    if v <= 1.5:
        return '<=1.5'
    if v <= 8:
        return '1.5-8'
    if v <= 12:
        return '8-12'
    return '>12'


def price_progress(m, entry):
    q = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy()
    c = num(q['close']).dropna() if 'close' in q else pd.Series(dtype='float64')
    if len(c) < 2 or c.iloc[0] <= 0:
        return np.nan
    return float(c.iloc[-1] / c.iloc[0] - 1.0) * 100.0


def stat(g):
    p = num(g['net_pct']).dropna()
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    return dict(
        trades=len(p),
        wins=int((p > 0).sum()),
        win_pct=float((p > 0).mean() * 100.0) if len(p) else 0.0,
        net_sum=float(p.sum()) if len(p) else 0.0,
        avg_net=float(p.mean()) if len(p) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss=float(p.min()) if len(p) else np.nan,
        median_zero=float(num(g['zero_cross_bars']).median()) if len(g) else np.nan,
        median_price_progress=float(num(g['price_progress_1m_pct']).median()) if len(g) else np.nan,
    )


def main():
    if not SRC.exists():
        raise FileNotFoundError(SRC)

    c = pd.read_csv(SRC)
    c['symbol'] = c['symbol'].astype(str).str.zfill(6)
    c['ready_time'] = pd.to_datetime(c['ready_time'])
    c['entry_time'] = pd.to_datetime(c['entry_time'])
    c['zero_cross_bars'] = num(c['zero_cross_bars'])
    c['net_pct'] = num(c['net_pct'])

    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    rows = []
    syms = list(c.groupby('symbol'))
    for i, (sym, g) in enumerate(syms, 1):
        print(f'[{i}/{len(syms)}] {sym}', flush=True)
        pf, _ = st.load_or_build_cache(sym, raw[sym], cfg, completed[sym])
        micro = h.build_micro(raw[sym], cfg)
        z, m = slow.add_slow_turn_features(pf, micro)

        for _, r in g.iterrows():
            ready = pd.Timestamp(r.ready_time)
            entry = pd.Timestamp(r.entry_time)
            q5 = z[(z.time <= ready) & (z.time >= ready - pd.Timedelta(minutes=6))].copy()
            q1 = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy()

            gap5_m = seq_monotonicity(num(q5['gap_delta'])) if 'gap_delta' in q5 else np.nan
            rsi5_m = seq_monotonicity(num(q5['rsi_slope'])) if 'rsi_slope' in q5 else np.nan
            gap1_m = seq_monotonicity(num(q1['macd_gap_delta_1m'])) if 'macd_gap_delta_1m' in q1 else np.nan
            rsi1_m = seq_monotonicity(num(q1['rsi_slope_1m'])) if 'rsi_slope_1m' in q1 else np.nan

            joint5 = min(gap5_m, rsi5_m) if np.isfinite(gap5_m) and np.isfinite(rsi5_m) else np.nan
            joint1 = min(gap1_m, rsi1_m) if np.isfinite(gap1_m) and np.isfinite(rsi1_m) else np.nan

            rows.append(dict(
                symbol=sym,
                ready_time=ready,
                entry_time=entry,
                zero_cross_bars=finite(r.zero_cross_bars),
                gap5_monotonicity=gap5_m,
                rsi5_monotonicity=rsi5_m,
                joint5_persistence=joint5,
                gap1_monotonicity=gap1_m,
                rsi1_monotonicity=rsi1_m,
                joint1_persistence=joint1,
                price_progress_1m_pct=price_progress(m, entry),
                net_pct=finite(r.net_pct),
            ))

    out = pd.DataFrame(rows)
    out['p5_bucket'] = [persistence_bucket(v) for v in out.joint5_persistence]
    out['p1_bucket'] = [persistence_bucket(v) for v in out.joint1_persistence]
    out['distance_bucket'] = [distance_bucket(v) for v in out.zero_cross_bars]

    summary = []

    # Main question: does 5m + 1m persistence jointly separate outcomes across all 96?
    for (b5, b1), g in out.groupby(['p5_bucket', 'p1_bucket']):
        summary.append(dict(view='ALL_P5_X_P1', distance_bucket='ALL', p5_bucket=b5, p1_bucket=b1, **stat(g)))

    # Secondary diagnostic only: is the same relation visible within distance regimes?
    for (db, b5, b1), g in out.groupby(['distance_bucket', 'p5_bucket', 'p1_bucket']):
        summary.append(dict(view='DIST_X_PERSISTENCE', distance_bucket=db, p5_bucket=b5, p1_bucket=b1, **stat(g)))

    # Cumulative, deliberately coarse checks; descriptive only, no threshold selection.
    for t5 in [0.60, 0.70, 0.80]:
        for t1 in [0.60, 0.70, 0.80]:
            g = out[(out.joint5_persistence >= t5) & (out.joint1_persistence >= t1)]
            if len(g):
                summary.append(dict(view='CUMULATIVE', distance_bucket='ALL',
                                    p5_bucket=f'>={t5:.2f}', p1_bucket=f'>={t1:.2f}', **stat(g)))

    s = pd.DataFrame(summary)
    s = s.sort_values(['view', 'distance_bucket', 'p5_bucket', 'p1_bucket']).reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_DETAIL, index=False)
    s.to_csv(OUT_SUMMARY, index=False)

    print('\n=== SLOW TURN PERSISTENCE SURFACE ===')
    print(f'SOURCE_TRADES={len(out)}')
    print('No rule changed. Persistence buckets are descriptive only.')

    print('\n=== ALL 96: 5M x 1M JOINT PERSISTENCE ===')
    print(s[s.view == 'ALL_P5_X_P1'].to_string(index=False))

    print('\n=== CUMULATIVE CHECKS ===')
    print(s[s.view == 'CUMULATIVE'].to_string(index=False))

    print('\n=== DISTANCE REGIMES ===')
    print(s[s.view == 'DIST_X_PERSISTENCE'].to_string(index=False))

    print('\nWROTE', OUT_DETAIL)
    print('WROTE', OUT_SUMMARY)


if __name__ == '__main__':
    main()
