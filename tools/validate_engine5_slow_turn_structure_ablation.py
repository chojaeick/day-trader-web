from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import tools.validate_engine5_v17c_5m_context_1m_trigger as h
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
ZERO_SRC = OUT_DIR / 'slow_turn_zero_cross_candidates.csv'
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT_CASES = OUT_DIR / 'slow_turn_structure_ablation_cases.csv'
OUT_RULES = OUT_DIR / 'slow_turn_structure_ablation_rules.csv'
OUT_EXTENSION = OUT_DIR / 'slow_turn_structure_ablation_extension.csv'
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


def safe_pct(a, b):
    try:
        a = float(a); b = float(b)
        if not np.isfinite(a) or not np.isfinite(b) or a == 0:
            return np.nan
        return (b / a - 1.0) * 100.0
    except Exception:
        return np.nan


def metric_window(m: pd.DataFrame, entry: pd.Timestamp):
    q = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy().sort_values('time')
    if q.empty:
        return {}
    close = num(q.get('close', pd.Series(index=q.index, dtype=float))).dropna()
    high = num(q.get('high', pd.Series(index=q.index, dtype=float))).dropna()
    low = num(q.get('low', pd.Series(index=q.index, dtype=float))).dropna()
    if not len(close):
        return {}
    total = safe_pct(close.iloc[0], close.iloc[-1]) if len(close) >= 2 else np.nan
    last1 = safe_pct(close.iloc[-2], close.iloc[-1]) if len(close) >= 2 else np.nan
    last2 = safe_pct(close.iloc[-3], close.iloc[-1]) if len(close) >= 3 else np.nan
    lo = float(low.min()) if len(low) else np.nan
    hi = float(high.max()) if len(high) else np.nan
    end = float(close.iloc[-1])
    return dict(
        close_progress_6m_pct=total,
        rise_from_6m_low_pct=safe_pct(lo, end),
        entry_vs_6m_high_pct=safe_pct(hi, end),
        last1m_return_pct=last1,
        last2m_return_pct=last2,
    )


def regime(z):
    z = finite(z)
    if not np.isfinite(z): return 'INVALID'
    if z <= 1.5: return 'NEAR_LE1_5'
    if z <= 8.0: return 'MID_1_5_8'
    if z <= 12.0: return 'BOUNDARY_8_12'
    return 'DEEP_GT12'


def stat(label, g):
    net = num(g['net_pct']).dropna()
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        rule=label,
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum=float(net.sum()) if len(net) else 0.0,
        avg_net=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss=float(net.min()) if len(net) else np.nan,
    )


def apply(df, label, mask):
    return stat(label, df[mask.fillna(False)])


def ext_bucket(v):
    v = finite(v)
    if not np.isfinite(v): return 'NA'
    if v < 1.5: return '<1.5%'
    if v < 3.0: return '1.5-3%'
    if v < 4.0: return '3-4%'
    return '>=4%'


def main():
    for p in [ZERO_SRC, PERSIST_SRC]:
        if not p.exists():
            raise FileNotFoundError(p)

    z = pd.read_csv(ZERO_SRC)
    p = pd.read_csv(PERSIST_SRC)
    for df in [z, p]:
        df['symbol'] = df['symbol'].astype(str).str.zfill(6)
        df['entry_time'] = pd.to_datetime(df['entry_time'])

    # Outcome source already contains fee-adjusted net_pct from the same 96-candidate simulation.
    keep_p = ['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    x = z.merge(p[keep_p], on=['symbol','entry_time'], how='inner', validate='one_to_one')
    if 'net_pct' not in x.columns:
        if 'pnl_pct' not in x.columns:
            raise KeyError('Need net_pct or pnl_pct in zero-cross source')
        x['net_pct'] = num(x['pnl_pct']) - FEE_RT_PCT

    raw = {n(k): v for k, v in load_data().items()}
    cfg = DoubleBollingerEngine5Config()
    micro_cache = {}
    ext = []
    for _, r in x.iterrows():
        sym = n(r.symbol)
        if sym not in micro_cache:
            micro_cache[sym] = h.build_micro(raw[sym], cfg).copy()
            micro_cache[sym]['time'] = pd.to_datetime(micro_cache[sym]['time'])
        ext.append(metric_window(micro_cache[sym], pd.Timestamp(r.entry_time)))
    x = pd.concat([x.reset_index(drop=True), pd.DataFrame(ext)], axis=1)
    x['regime'] = [regime(v) for v in x['zero_cross_bars']]
    x['extension_bucket'] = [ext_bucket(v) for v in x['close_progress_6m_pct']]
    x.to_csv(OUT_CASES, index=False)

    p5 = num(x['joint5_persistence'])
    p1 = num(x['joint1_persistence'])
    px = num(x['price_progress_1m_pct'])
    gd = num(x['gap_delta_5m'])
    rs = num(x['rsi_slope_5m'])
    e6 = num(x['close_progress_6m_pct'])

    rules = []
    near = x.regime.eq('NEAR_LE1_5')
    mid = x.regime.eq('MID_1_5_8')
    bnd = x.regime.eq('BOUNDARY_8_12')
    deep = x.regime.eq('DEEP_GT12')

    # NEAR: test whether price confirmation matters, then whether pre-entry extension is the missing structural dimension.
    rules += [
        apply(x, 'NEAR_ALL', near),
        apply(x, 'NEAR_PX075', near & (px >= 0.75)),
        apply(x, 'NEAR_PX075_EXT_LT4', near & (px >= 0.75) & (e6 < 4.0)),
        apply(x, 'NEAR_PX075_EXT_LT3', near & (px >= 0.75) & (e6 < 3.0)),
    ]

    # MID: leave-one-component-out. A structural component is useful only if removing it degrades quality.
    rules += [
        apply(x, 'MID_ALL', mid),
        apply(x, 'MID_BASE_P5P1PX', mid & (p5 >= 0.60) & (p1 >= 0.60) & (px >= 1.0)),
        apply(x, 'MID_NO_P5', mid & (p1 >= 0.60) & (px >= 1.0)),
        apply(x, 'MID_NO_P1', mid & (p5 >= 0.60) & (px >= 1.0)),
        apply(x, 'MID_NO_PX', mid & (p5 >= 0.60) & (p1 >= 0.60)),
    ]

    # BOUNDARY: separate absolute 5m propulsion from actual 1m price confirmation and persistence.
    strength = (gd >= 30.0) & (rs >= 10.0)
    rules += [
        apply(x, 'BOUNDARY_ALL', bnd),
        apply(x, 'BOUNDARY_BASE_STRENGTH_PX', bnd & strength & (px >= 1.5)),
        apply(x, 'BOUNDARY_NO_MACD', bnd & (rs >= 10.0) & (px >= 1.5)),
        apply(x, 'BOUNDARY_NO_RSI', bnd & (gd >= 30.0) & (px >= 1.5)),
        apply(x, 'BOUNDARY_NO_PX', bnd & strength),
        apply(x, 'BOUNDARY_PLUS_PERSIST', bnd & strength & (px >= 1.5) & (p5 >= 0.60) & (p1 >= 0.60)),
    ]

    # DEEP remains a separate reversal family. These rows are diagnostic, not candidates for gradual-turn freeze.
    rules += [
        apply(x, 'DEEP_ALL', deep),
        apply(x, 'DEEP_PERSIST_60_60', deep & (p5 >= 0.60) & (p1 >= 0.60)),
        apply(x, 'DEEP_PERSIST_80_70', deep & (p5 >= 0.80) & (p1 >= 0.70)),
        apply(x, 'DEEP_STRENGTH', deep & strength),
        apply(x, 'DEEP_STRENGTH_PERSIST', deep & strength & (p5 >= 0.80) & (p1 >= 0.70)),
    ]

    rule_df = pd.DataFrame(rules)
    rule_df.to_csv(OUT_RULES, index=False)

    ext_rows = []
    order = ['NEAR_LE1_5','MID_1_5_8','BOUNDARY_8_12','DEEP_GT12']
    for rg in order:
        q = x[x.regime.eq(rg)]
        for eb, g in q.groupby('extension_bucket', dropna=False):
            s = stat(f'{rg}|{eb}', g)
            s['regime'] = rg; s['extension_bucket'] = eb
            ext_rows.append(s)
    ext_df = pd.DataFrame(ext_rows)
    ext_df.to_csv(OUT_EXTENSION, index=False)

    print('\n=== SLOW TURN STRUCTURE ABLATION ===')
    print(f'Candidate population: {len(x)} (expected 96). No V20 rule changed.')
    print('Purpose: validate component roles, not optimize exact thresholds.')

    print('\n=== REGIME COUNTS / RAW OUTCOME ===')
    reg_rows = [stat(rg, x[x.regime.eq(rg)]) for rg in order]
    print(pd.DataFrame(reg_rows).to_string(index=False))

    print('\n=== STRUCTURAL ABLATIONS ===')
    print(rule_df.to_string(index=False))

    print('\n=== PRICE EXTENSION BY REGIME ===')
    show = ext_df[['regime','extension_bucket','trades','wins','win_pct','net_sum','pf','max_loss']]
    print(show.to_string(index=False))

    print('\nReading guide:')
    print('- MID: compare BASE vs NO_P5 / NO_P1 / NO_PX.')
    print('- BOUNDARY: compare BASE vs NO_MACD / NO_RSI / NO_PX / PLUS_PERSIST.')
    print('- NEAR: EXT rows test whether already-extended price is the missing dimension.')
    print('- DEEP is not part of gradual-turn freeze; keep it separate unless evidence becomes robust.')
    print('WROTE', OUT_CASES)
    print('WROTE', OUT_RULES)
    print('WROTE', OUT_EXTENSION)


if __name__ == '__main__':
    main()
