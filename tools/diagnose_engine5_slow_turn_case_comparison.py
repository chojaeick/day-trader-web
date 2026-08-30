from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_slow_turn_prototype as slow
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
SRC = OUT_DIR / 'slow_turn_zero_cross_candidates.csv'
OUT = OUT_DIR / 'slow_turn_case_comparison.csv'


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


def slope_metrics(q):
    if q.empty:
        return dict(slope_first=np.nan, slope_last=np.nan, slope_gain=np.nan,
                    slope_pos_ratio=np.nan, slope_monotonicity=np.nan)
    s = num(q['mid_slope8']).dropna()
    if len(s) < 2:
        return dict(slope_first=finite(s.iloc[0]) if len(s) else np.nan,
                    slope_last=finite(s.iloc[-1]) if len(s) else np.nan,
                    slope_gain=np.nan, slope_pos_ratio=np.nan, slope_monotonicity=np.nan)
    d = s.diff().dropna()
    pos_ratio = float((d > 0).mean()) if len(d) else np.nan
    total_pos = float(d[d > 0].sum()) if (d > 0).any() else 0.0
    total_neg = float(-d[d < 0].sum()) if (d < 0).any() else 0.0
    mono = total_pos / (total_pos + total_neg) if (total_pos + total_neg) > 0 else np.nan
    return dict(slope_first=float(s.iloc[0]), slope_last=float(s.iloc[-1]),
                slope_gain=float(s.iloc[-1] - s.iloc[0]), slope_pos_ratio=pos_ratio,
                slope_monotonicity=mono)


def micro_metrics(q, entry_time):
    if q.empty:
        return {}
    z = q[q.time <= entry_time].tail(5).copy()
    if z.empty:
        return {}
    gd = num(z['macd_gap_delta_1m']).dropna()
    rs = num(z['rsi_slope_1m']).dropna()
    close = num(z['close']).dropna()
    low = num(z['low']).dropna()
    gap_pos = float((gd > 0).mean()) if len(gd) else np.nan
    rsi_pos = float((rs > 0).mean()) if len(rs) else np.nan
    gap_first, gap_last = (float(gd.iloc[0]), float(gd.iloc[-1])) if len(gd) else (np.nan, np.nan)
    rsi_first, rsi_last = (float(rs.iloc[0]), float(rs.iloc[-1])) if len(rs) else (np.nan, np.nan)
    close_prog = float(close.iloc[-1] / close.iloc[0] - 1.0) * 100.0 if len(close) >= 2 and close.iloc[0] > 0 else np.nan
    hl_row = q[q.time == entry_time]
    hl = bool(hl_row['higher_low'].iloc[0]) if len(hl_row) else False
    br = bool(hl_row['higher_high_break'].iloc[0]) if len(hl_row) else False
    prior_low = finite(hl_row['prior_low_3'].iloc[0]) if len(hl_row) and 'prior_low_3' in hl_row else np.nan
    px = finite(hl_row['close'].iloc[0]) if len(hl_row) else np.nan
    hl_buffer_pct = (px / prior_low - 1.0) * 100.0 if np.isfinite(px) and np.isfinite(prior_low) and prior_low > 0 else np.nan
    return dict(micro_gap_first=gap_first, micro_gap_last=gap_last,
                micro_gap_rise=gap_last-gap_first if np.isfinite(gap_first) and np.isfinite(gap_last) else np.nan,
                micro_gap_pos_ratio=gap_pos, micro_rsi_first=rsi_first, micro_rsi_last=rsi_last,
                micro_rsi_rise=rsi_last-rsi_first if np.isfinite(rsi_first) and np.isfinite(rsi_last) else np.nan,
                micro_rsi_pos_ratio=rsi_pos, micro_close_progress_pct=close_prog,
                entry_higher_low=hl, entry_high_break=br, hl_buffer_pct=hl_buffer_pct)


def main():
    if not SRC.exists():
        raise FileNotFoundError(SRC)
    c = pd.read_csv(SRC)
    c['ready_time'] = pd.to_datetime(c['ready_time'])
    c['entry_time'] = pd.to_datetime(c['entry_time'])
    c['symbol'] = c['symbol'].astype(str).str.zfill(6)
    c['net_pct'] = num(c['net_pct'])
    c['zero_cross_bars'] = num(c['zero_cross_bars'])
    c['gap_delta_5m'] = num(c['gap_delta_5m'])

    core = c[(c.zero_cross_bars <= 8) & (c.gap_delta_5m <= 40)].copy()
    boundary = c[(c.zero_cross_bars > 8) & (c.zero_cross_bars <= 12)].copy()
    selected = pd.concat([core, boundary], ignore_index=True).drop_duplicates(['symbol','entry_time'])
    selected['group'] = np.where(selected.zero_cross_bars <= 8, 'CORE_LE8_MACD_LE40',
                         np.where(selected.net_pct > 0, 'BOUNDARY_8_12_WIN', 'BOUNDARY_8_12_LOSS'))

    print('=== SLOW TURN CASE COMPARISON ===')
    print(f'CORE={len(core)} BOUNDARY={len(boundary)} SELECTED={len(selected)}')
    print('No rule changed. Compare pre-entry continuity and price structure only.\n')

    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    rows = []
    for _, r in selected.sort_values(['group','entry_time']).iterrows():
        sym = n(r.symbol)
        pf, _ = st.load_or_build_cache(sym, raw[sym], cfg, completed[sym])
        micro = h.build_micro(raw[sym], cfg)
        z, m = slow.add_slow_turn_features(pf, micro)
        ready = pd.Timestamp(r.ready_time)
        entry = pd.Timestamp(r.entry_time)
        q5 = z[(z.time <= ready) & (z.time >= ready - pd.Timedelta(minutes=5))].copy()
        q1 = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy()
        sm = slope_metrics(q5)
        mm = micro_metrics(q1, entry)
        rows.append(dict(group=r.group, symbol=sym, ready_time=ready, entry_time=entry,
                         zero_cross_bars=finite(r.zero_cross_bars), gap_delta_5m=finite(r.gap_delta_5m),
                         rsi_slope_5m=finite(r.rsi_slope_5m), net_pct=finite(r.net_pct), win=bool(r.net_pct > 0),
                         **sm, **mm))

    out = pd.DataFrame(rows)
    cols = ['group','symbol','ready_time','entry_time','zero_cross_bars','gap_delta_5m','rsi_slope_5m',
            'slope_first','slope_last','slope_gain','slope_pos_ratio','slope_monotonicity',
            'micro_gap_first','micro_gap_last','micro_gap_rise','micro_gap_pos_ratio',
            'micro_rsi_first','micro_rsi_last','micro_rsi_rise','micro_rsi_pos_ratio',
            'micro_close_progress_pct','entry_higher_low','entry_high_break','hl_buffer_pct','net_pct','win']
    print(out[cols].to_string(index=False))

    print('\n=== GROUP MEANS ===')
    metrics = ['zero_cross_bars','gap_delta_5m','rsi_slope_5m','slope_gain','slope_pos_ratio','slope_monotonicity',
               'micro_gap_rise','micro_gap_pos_ratio','micro_rsi_rise','micro_rsi_pos_ratio',
               'micro_close_progress_pct','hl_buffer_pct','net_pct']
    gm = out.groupby('group')[metrics].mean(numeric_only=True)
    print(gm.to_string())

    out.to_csv(OUT, index=False)
    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
