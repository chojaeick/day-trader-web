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
OUT_DETAIL = OUT_DIR / 'slow_turn_joint_strength_candidates.csv'
OUT_SUMMARY = OUT_DIR / 'slow_turn_joint_strength_summary.csv'
FEE_RT_PCT = 0.25


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


def stat(df):
    p = num(df['net_pct']).dropna()
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    return dict(
        trades=len(p), wins=int((p > 0).sum()),
        win_pct=float((p > 0).mean() * 100.0) if len(p) else 0.0,
        net_sum=float(p.sum()) if len(p) else 0.0,
        avg_net=float(p.mean()) if len(p) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss=float(p.min()) if len(p) else np.nan,
    )


def strength_bucket(macd, rsi):
    if not np.isfinite(macd) or not np.isfinite(rsi):
        return 'NA'
    # Intentionally coarse. This is a diagnostic, not a trading rule.
    if macd >= 30 and rsi >= 10:
        return 'STRONG_BOTH'
    if macd >= 20 and rsi >= 5:
        return 'MID_BOTH'
    if macd >= 10 and rsi > 0:
        return 'LIGHT_BOTH'
    return 'WEAK'


def distance_bucket(z):
    if not np.isfinite(z):
        return 'NA'
    if z <= 1.5:
        return '<=1.5'
    if z <= 8:
        return '1.5-8'
    if z <= 12:
        return '8-12'
    return '>12'


def price_bucket(v):
    if not np.isfinite(v):
        return 'NA'
    if v >= 3.0:
        return '>=3%'
    if v >= 1.5:
        return '1.5-3%'
    if v >= 0.5:
        return '0.5-1.5%'
    return '<0.5%'


def micro_price_progress(m, entry_time):
    q = m[(m.time <= entry_time) & (m.time >= entry_time - pd.Timedelta(minutes=4))].copy()
    if q.empty:
        return np.nan
    c = num(q['close']).dropna()
    if len(c) < 2 or c.iloc[0] <= 0:
        return np.nan
    return float(c.iloc[-1] / c.iloc[0] - 1.0) * 100.0


def main():
    if not SRC.exists():
        raise FileNotFoundError(SRC)

    c = pd.read_csv(SRC)
    c['symbol'] = c['symbol'].astype(str).str.zfill(6)
    c['ready_time'] = pd.to_datetime(c['ready_time'])
    c['entry_time'] = pd.to_datetime(c['entry_time'])
    c['zero_cross_bars'] = num(c['zero_cross_bars'])
    c['gap_delta_5m'] = num(c['gap_delta_5m'])
    c['rsi_slope_5m'] = num(c['rsi_slope_5m'])
    c['net_pct'] = num(c['net_pct'])

    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    # Reuse cached/provisional machinery only to reconstruct the same micro frame.
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    rows = []
    for i, (sym, g) in enumerate(c.groupby('symbol'), 1):
        print(f'[{i}/{c.symbol.nunique()}] {sym}', flush=True)
        pf, _ = st.load_or_build_cache(sym, raw[sym], cfg, completed[sym])
        micro = h.build_micro(raw[sym], cfg)
        _, m = slow.add_slow_turn_features(pf, micro)
        for _, r in g.iterrows():
            prog = micro_price_progress(m, pd.Timestamp(r.entry_time))
            rows.append(dict(
                symbol=sym, ready_time=r.ready_time, entry_time=r.entry_time,
                zero_cross_bars=finite(r.zero_cross_bars),
                gap_delta_5m=finite(r.gap_delta_5m),
                rsi_slope_5m=finite(r.rsi_slope_5m),
                micro_price_progress_pct=prog,
                net_pct=finite(r.net_pct),
                win=bool(finite(r.net_pct) > 0),
            ))

    out = pd.DataFrame(rows)
    out['distance_bucket'] = [distance_bucket(v) for v in num(out.zero_cross_bars)]
    out['strength_bucket'] = [strength_bucket(m, r) for m, r in zip(num(out.gap_delta_5m), num(out.rsi_slope_5m))]
    out['price_bucket'] = [price_bucket(v) for v in num(out.micro_price_progress_pct)]

    summary = []
    # 1) Distance x joint 5m strength
    for (db, sb), g in out.groupby(['distance_bucket', 'strength_bucket']):
        summary.append(dict(view='DIST_X_5M_STRENGTH', distance_bucket=db, strength_bucket=sb,
                            price_bucket='ALL', **stat(g),
                            median_zero=float(num(g.zero_cross_bars).median()),
                            median_macd=float(num(g.gap_delta_5m).median()),
                            median_rsi=float(num(g.rsi_slope_5m).median()),
                            median_price_progress=float(num(g.micro_price_progress_pct).median())))
    # 2) Distance x real 1m price progress
    for (db, pb), g in out.groupby(['distance_bucket', 'price_bucket']):
        summary.append(dict(view='DIST_X_1M_PRICE', distance_bucket=db, strength_bucket='ALL',
                            price_bucket=pb, **stat(g),
                            median_zero=float(num(g.zero_cross_bars).median()),
                            median_macd=float(num(g.gap_delta_5m).median()),
                            median_rsi=float(num(g.rsi_slope_5m).median()),
                            median_price_progress=float(num(g.micro_price_progress_pct).median())))
    # 3) Key near-transition slices: distance <=8 versus 8-12, all three dimensions.
    key = out[out.distance_bucket.isin(['<=1.5', '1.5-8', '8-12'])].copy()
    for (db, sb, pb), g in key.groupby(['distance_bucket', 'strength_bucket', 'price_bucket']):
        summary.append(dict(view='KEY_3WAY', distance_bucket=db, strength_bucket=sb,
                            price_bucket=pb, **stat(g),
                            median_zero=float(num(g.zero_cross_bars).median()),
                            median_macd=float(num(g.gap_delta_5m).median()),
                            median_rsi=float(num(g.rsi_slope_5m).median()),
                            median_price_progress=float(num(g.micro_price_progress_pct).median())))

    s = pd.DataFrame(summary)
    s = s.sort_values(['view', 'distance_bucket', 'net_sum'], ascending=[True, True, False])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_DETAIL, index=False)
    s.to_csv(OUT_SUMMARY, index=False)

    print('\n=== SLOW TURN JOINT-STRENGTH DIAGNOSTIC ===')
    print(f'SOURCE_TRADES={len(out)}')
    print('No rule changed. Coarse buckets are descriptive only.')
    print('\n=== DISTANCE x 5M JOINT STRENGTH ===')
    print(s[s.view == 'DIST_X_5M_STRENGTH'].to_string(index=False))
    print('\n=== DISTANCE x 1M PRICE PROGRESS ===')
    print(s[s.view == 'DIST_X_1M_PRICE'].to_string(index=False))
    print('\n=== KEY 3-WAY: <=8 AND 8-12 ONLY ===')
    print(s[s.view == 'KEY_3WAY'].to_string(index=False))
    print('\nWROTE', OUT_DETAIL)
    print('WROTE', OUT_SUMMARY)


if __name__ == '__main__':
    main()
