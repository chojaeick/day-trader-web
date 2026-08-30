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
SRC = OUT_DIR / 'slow_turn_joint_strength_candidates.csv'
OUT = OUT_DIR / 'slow_turn_deep_strong6_comparison.csv'


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


def strength_bucket(macd, rsi):
    if not np.isfinite(macd) or not np.isfinite(rsi):
        return 'NA'
    if macd >= 30 and rsi >= 10:
        return 'STRONG_BOTH'
    if macd >= 20 and rsi >= 5:
        return 'MID_BOTH'
    if macd >= 10 and rsi > 0:
        return 'LIGHT_BOTH'
    return 'WEAK'


def seq_metrics(vals):
    s = pd.Series(vals, dtype='float64').dropna()
    if len(s) < 2:
        return dict(first=finite(s.iloc[0]) if len(s) else np.nan,
                    last=finite(s.iloc[-1]) if len(s) else np.nan,
                    delta=np.nan, pos_ratio=np.nan, monotonicity=np.nan)
    d = s.diff().dropna()
    pos = float((d > 0).mean()) if len(d) else np.nan
    up = float(d[d > 0].sum()) if (d > 0).any() else 0.0
    dn = float(-d[d < 0].sum()) if (d < 0).any() else 0.0
    mono = up / (up + dn) if (up + dn) > 0 else np.nan
    return dict(first=float(s.iloc[0]), last=float(s.iloc[-1]),
                delta=float(s.iloc[-1] - s.iloc[0]),
                pos_ratio=pos, monotonicity=mono)


def pct_progress(s):
    x = num(s).dropna()
    if len(x) < 2 or x.iloc[0] <= 0:
        return np.nan
    return float(x.iloc[-1] / x.iloc[0] - 1.0) * 100.0


def case_metrics(z, m, ready, entry):
    q5 = z[(z.time <= ready) & (z.time >= ready - pd.Timedelta(minutes=6))].copy()
    q1 = m[(m.time <= entry) & (m.time >= entry - pd.Timedelta(minutes=6))].copy()

    sm = seq_metrics(num(q5['mid_slope8'])) if 'mid_slope8' in q5 else seq_metrics([])
    gm = seq_metrics(num(q5['gap_delta'])) if 'gap_delta' in q5 else seq_metrics([])
    rm = seq_metrics(num(q5['rsi_slope'])) if 'rsi_slope' in q5 else seq_metrics([])

    mg = seq_metrics(num(q1['macd_gap_delta_1m'])) if 'macd_gap_delta_1m' in q1 else seq_metrics([])
    mr = seq_metrics(num(q1['rsi_slope_1m'])) if 'rsi_slope_1m' in q1 else seq_metrics([])

    close_prog = pct_progress(q1['close']) if 'close' in q1 else np.nan
    low_prog = pct_progress(q1['low']) if 'low' in q1 else np.nan

    last = q1[q1.time == entry]
    if last.empty:
        last = q1.tail(1)
    hl = bool(last['higher_low'].iloc[0]) if len(last) and 'higher_low' in last else False
    hh = bool(last['higher_high_break'].iloc[0]) if len(last) and 'higher_high_break' in last else False
    prior_low = finite(last['prior_low_3'].iloc[0]) if len(last) and 'prior_low_3' in last else np.nan
    px = finite(last['close'].iloc[0]) if len(last) and 'close' in last else np.nan
    hl_buffer = (px / prior_low - 1.0) * 100.0 if np.isfinite(px) and np.isfinite(prior_low) and prior_low > 0 else np.nan

    return dict(
        slope_first=sm['first'], slope_last=sm['last'], slope_window_delta=sm['delta'],
        slope_pos_ratio=sm['pos_ratio'], slope_monotonicity=sm['monotonicity'],
        gap5_first=gm['first'], gap5_last=gm['last'], gap5_delta=gm['delta'],
        gap5_pos_ratio=gm['pos_ratio'], gap5_monotonicity=gm['monotonicity'],
        rsi5_first=rm['first'], rsi5_last=rm['last'], rsi5_delta=rm['delta'],
        rsi5_pos_ratio=rm['pos_ratio'], rsi5_monotonicity=rm['monotonicity'],
        gap1_first=mg['first'], gap1_last=mg['last'], gap1_delta=mg['delta'],
        gap1_pos_ratio=mg['pos_ratio'], gap1_monotonicity=mg['monotonicity'],
        rsi1_first=mr['first'], rsi1_last=mr['last'], rsi1_delta=mr['delta'],
        rsi1_pos_ratio=mr['pos_ratio'], rsi1_monotonicity=mr['monotonicity'],
        close_progress_1m_pct=close_prog, low_progress_1m_pct=low_prog,
        entry_higher_low=hl, entry_high_break=hh, hl_buffer_pct=hl_buffer,
    )


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
    c['strength_bucket'] = [strength_bucket(m, r) for m, r in zip(c.gap_delta_5m, c.rsi_slope_5m)]

    sel = c[(c.zero_cross_bars > 12) & (c.strength_bucket == 'STRONG_BOTH')].copy()
    sel = sel.sort_values('entry_time').reset_index(drop=True)

    print('=== SLOW TURN >12 + STRONG_BOTH CASE COMPARISON ===')
    print(f'SELECTED={len(sel)}  WINS={(sel.net_pct > 0).sum()}  LOSSES={(sel.net_pct <= 0).sum()}')
    print('No rule changed. Direct case comparison only.\n')

    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}

    rows = []
    for _, r in sel.iterrows():
        sym = n(r.symbol)
        pf, _ = st.load_or_build_cache(sym, raw[sym], cfg, completed[sym])
        micro = h.build_micro(raw[sym], cfg)
        z, m = slow.add_slow_turn_features(pf, micro)
        cm = case_metrics(z, m, pd.Timestamp(r.ready_time), pd.Timestamp(r.entry_time))
        rows.append(dict(
            result='WIN' if r.net_pct > 0 else 'LOSS',
            symbol=sym, ready_time=r.ready_time, entry_time=r.entry_time,
            zero_cross_bars=finite(r.zero_cross_bars),
            gap_delta_5m=finite(r.gap_delta_5m), rsi_slope_5m=finite(r.rsi_slope_5m),
            source_price_progress_pct=finite(r.micro_price_progress_pct),
            net_pct=finite(r.net_pct), **cm
        ))

    out = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)

    compact = ['result','symbol','ready_time','entry_time','zero_cross_bars','gap_delta_5m','rsi_slope_5m',
               'slope_last','slope_window_delta','slope_monotonicity',
               'gap5_delta','gap5_monotonicity','rsi5_delta','rsi5_monotonicity',
               'gap1_delta','gap1_monotonicity','rsi1_delta','rsi1_monotonicity',
               'close_progress_1m_pct','low_progress_1m_pct','hl_buffer_pct','net_pct']
    print(out[compact].to_string(index=False))

    metrics = ['zero_cross_bars','gap_delta_5m','rsi_slope_5m','slope_last','slope_window_delta','slope_monotonicity',
               'gap5_delta','gap5_monotonicity','rsi5_delta','rsi5_monotonicity',
               'gap1_delta','gap1_monotonicity','rsi1_delta','rsi1_monotonicity',
               'close_progress_1m_pct','low_progress_1m_pct','hl_buffer_pct','net_pct']
    print('\n=== WIN vs LOSS MEANS ===')
    print(out.groupby('result')[metrics].mean(numeric_only=True).to_string())

    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
