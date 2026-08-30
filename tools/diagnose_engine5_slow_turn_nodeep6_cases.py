from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.validate_engine5_slow_turn_regime_integrated as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT = OUT_DIR / 'slow_turn_nodeep6_cases.csv'
THRESHOLD = 50
FEE_RT_PCT = 0.25


def n(x):
    return str(x).zfill(6)


def num(x):
    return pd.to_numeric(x, errors='coerce')


def main():
    if not PERSIST_SRC.exists():
        raise FileNotFoundError(PERSIST_SRC)

    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}

    parts = []
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        q = zd.build_candidates(s, pf, micros[s], scored[s])
        if len(q):
            parts.append(q)
    cand = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    cand['symbol'] = cand['symbol'].astype(str).str.zfill(6)
    cand['entry_time'] = pd.to_datetime(cand['entry_time'])

    diag = pd.read_csv(PERSIST_SRC)
    diag['symbol'] = diag['symbol'].astype(str).str.zfill(6)
    diag['entry_time'] = pd.to_datetime(diag['entry_time'])
    dcols = ['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    x = cand.merge(diag[dcols], on=['symbol','entry_time'], how='inner', validate='one_to_one')

    sel = integ.select_policy(x, 'NO_DEEP').copy()
    sev = zd.event_stream(sel)
    trades = multi.simulate_multi(packed, sev, states, THRESHOLD).copy()
    trades['symbol'] = trades['symbol'].astype(str).str.zfill(6)
    trades['entry_time'] = pd.to_datetime(trades['entry_time'])
    if 'exit_time' in trades.columns:
        trades['exit_time'] = pd.to_datetime(trades['exit_time'])
    trades['net_pct'] = num(trades['pnl_pct']) - FEE_RT_PCT

    # Simulator outputs are not guaranteed to carry a textual exit reason.
    # Keep the diagnostic robust and show NA when the column is unavailable.
    if 'exit_reason' not in trades.columns:
        trades['exit_reason'] = pd.NA

    merge_cols = ['symbol','entry_time']
    optional = ['exit_time','entry_price','exit_price','pnl_pct','net_pct','exit_reason']
    trade_cols = merge_cols + [c for c in optional if c in trades.columns]

    out = sel.drop(columns=['event'], errors='ignore').merge(
        trades[trade_cols],
        on=merge_cols, how='left', validate='one_to_one', suffixes=('','_sim')
    )
    out['result'] = np.where(num(out['net_pct']) > 0, 'WIN', 'LOSS')

    cols = [
        'result','symbol','regime','ready_time','entry_time','exit_time',
        'entry_price_sim','exit_price','net_pct','exit_reason',
        'zero_cross_bars','mid_slope8','gap_delta_5m','rsi_slope_5m',
        'joint5_persistence','joint1_persistence','price_progress_1m_pct',
        'gap_pos_ratio_1m','rsi_pos_ratio_1m'
    ]
    cols = [c for c in cols if c in out.columns]
    out = out.sort_values(['entry_time','symbol']).reset_index(drop=True)
    out.to_csv(OUT, index=False)

    print('\n=== SLOW TURN NO_DEEP 6 CASES ===')
    print(f'CASES={len(out)} WINS={(out.net_pct > 0).sum()} LOSSES={(out.net_pct <= 0).sum()} NET={num(out.net_pct).sum():.6f}%')
    print(out[cols].to_string(index=False))

    print('\n=== REGIME SUMMARY ===')
    g = out.groupby('regime', dropna=False)['net_pct'].agg(['count', lambda s: int((s > 0).sum()), 'sum', 'mean']).reset_index()
    g.columns = ['regime','trades','wins','net_sum','avg_net']
    print(g.to_string(index=False))
    print('\nNOTE: exit_reason=NA means this simulator output did not expose a reason column; it is not an unknown trade outcome.')
    print('WROTE', OUT)


if __name__ == '__main__':
    main()
