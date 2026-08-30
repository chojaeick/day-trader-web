from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_slow_turn_prototype as slow
import tools.validate_engine5_slow_turn_regime_integrated as ri
import tools.validate_engine5_slow_turn_structure_ablation as ab
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = OUT_DIR / 'slow_turn_provisional_full_summary.csv'
OUT_SELECTED = OUT_DIR / 'slow_turn_provisional_full_selected.csv'
OUT_EXTRA_TRADES = OUT_DIR / 'slow_turn_provisional_full_extra_trades.csv'
OUT_MERGED_TRADES = OUT_DIR / 'slow_turn_provisional_full_merged_trades.csv'
THRESHOLD = 50
FEE_RT_PCT = 0.25

# Provisional structural settings. These are not production-frozen thresholds.
NEAR_PX_MIN = 0.75
NEAR_EXTENSION_MAX = 4.0  # loose edge: current <3 and <4 samples are identical; avoid tighter overfit.
MID_P5_MIN = 0.60
MID_P1_MIN = 0.60
MID_PX_MIN = 1.00
BOUNDARY_MACD_MIN = 30.0
BOUNDARY_RSI_MIN = 10.0
BOUNDARY_PX_MIN = 1.50


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


def stat(label, trades):
    p = num(trades['pnl_pct']).dropna() if len(trades) else pd.Series(dtype=float)
    net = p - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        label=label,
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
    )


def classify_and_select(r):
    z = finite(r.zero_cross_bars)
    p5 = finite(r.joint5_persistence)
    p1 = finite(r.joint1_persistence)
    px = finite(r.price_progress_1m_pct)
    gd = finite(r.gap_delta_5m)
    rs = finite(r.rsi_slope_5m)
    ext6 = finite(r.close_progress_6m_pct)

    if not np.isfinite(z):
        return False, 'INVALID'

    if z <= 1.5:
        ok = (
            np.isfinite(px) and px >= NEAR_PX_MIN
            and np.isfinite(ext6) and ext6 < NEAR_EXTENSION_MAX
        )
        return bool(ok), 'NEAR_LE1_5'

    if z <= 8.0:
        ok = (
            np.isfinite(p5) and p5 >= MID_P5_MIN
            and np.isfinite(p1) and p1 >= MID_P1_MIN
            and np.isfinite(px) and px >= MID_PX_MIN
        )
        return bool(ok), 'MID_1_5_8'

    if z <= 12.0:
        ok = (
            np.isfinite(gd) and gd >= BOUNDARY_MACD_MIN
            and np.isfinite(rs) and rs >= BOUNDARY_RSI_MIN
            and np.isfinite(px) and px >= BOUNDARY_PX_MIN
        )
        return bool(ok), 'BOUNDARY_8_12'

    # Deep negative slope is deliberately excluded from gradual-turn.
    return False, 'DEEP_GT12'


def main():
    if not PERSIST_SRC.exists():
        raise FileNotFoundError(
            f'{PERSIST_SRC} not found. Run tools.diagnose_engine5_slow_turn_persistence_surface first.'
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}

    # Frozen V20 stream.
    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength, raw_min=52.0, rel_min=1.45)
    v20_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)
    sv20 = stat('V20_FROZEN', v20_trades)

    # Hard regression guard: stop if the protected baseline moved.
    v20_ok = (
        sv20['trades'] == 39
        and sv20['wins'] == 17
        and abs(sv20['net_sum_pct'] - 20.012131) < 1e-4
        and abs(sv20['pf'] - 1.86292) < 1e-4
    )
    if not v20_ok:
        print('V20 REGRESSION FAILURE. Abort slow-turn interpretation.')
        print(pd.DataFrame([sv20]).to_string(index=False))
        raise SystemExit(2)

    # Reconstruct the same full slow-turn candidate population from all loaded history.
    base_cand = ri.reconstruct_base_candidates(raw, cfg, scored, completed, micros)
    base_cand['symbol'] = base_cand['symbol'].astype(str).str.zfill(6)
    base_cand['entry_time'] = pd.to_datetime(base_cand['entry_time'])

    persist = pd.read_csv(PERSIST_SRC)
    persist['symbol'] = persist['symbol'].astype(str).str.zfill(6)
    persist['entry_time'] = pd.to_datetime(persist['entry_time'])
    keep = ['symbol','entry_time','joint5_persistence','joint1_persistence','price_progress_1m_pct']
    x = base_cand.merge(persist[keep], on=['symbol','entry_time'], how='inner', validate='one_to_one')

    # Add pre-entry 6-minute price extension to every candidate.
    ext_rows = []
    for _, r in x.iterrows():
        sym = n(r.symbol)
        m = micros[sym].copy()
        m['time'] = pd.to_datetime(m['time'])
        ext_rows.append(ab.metric_window(m, pd.Timestamp(r.entry_time)))
    x = pd.concat([x.reset_index(drop=True), pd.DataFrame(ext_rows)], axis=1)

    keep_mask = []
    regimes = []
    for _, r in x.iterrows():
        ok, rg = classify_and_select(r)
        keep_mask.append(ok)
        regimes.append(rg)
    x['regime'] = regimes
    selected = x[np.asarray(keep_mask, dtype=bool)].copy()

    sev = zd.event_stream(selected)
    extra_trades = multi.simulate_multi(packed, sev, states, THRESHOLD)
    merged_stream = slow.merge_streams(ev20, sev)
    merged_trades = multi.simulate_multi(packed, merged_stream, states, THRESHOLD)

    sextra = stat('SLOW_TURN_ADDED', extra_trades)
    smerged = stat('V20_PLUS_SLOW_TURN', merged_trades)

    # Signal overlap/regression checks.
    v20_keys = {(pd.Timestamp(ts), n(c[0])) for ts, cs in ev20.items() for c in cs}
    overlap = sum((pd.Timestamp(r.entry_time), n(r.symbol)) in v20_keys for _, r in selected.iterrows())
    regime_counts = selected['regime'].value_counts().to_dict() if len(selected) else {}

    summary = pd.DataFrame([
        {**sv20, 'signals': len(v20_keys), 'overlaps_v20': 0},
        {**sextra, 'signals': len(selected), 'overlaps_v20': overlap},
        {**smerged, 'signals': len(v20_keys) + len(selected) - overlap, 'overlaps_v20': overlap},
    ])
    summary.to_csv(OUT_SUMMARY, index=False)

    selected.drop(columns=['event'], errors='ignore').to_csv(OUT_SELECTED, index=False)
    extra_trades.to_csv(OUT_EXTRA_TRADES, index=False)
    merged_trades.to_csv(OUT_MERGED_TRADES, index=False)

    print('\n=== PROVISIONAL SLOW-TURN FULL-HISTORY VALIDATION ===')
    print(f'Loaded symbols: {len(raw)}')
    print(f'Full reconstructed slow-turn population: {len(x)}')
    print('V20 regression guard: PASS (39 trades / 17 wins / +20.012131% / PF 1.86292)')
    print('\nProvisional structure:')
    print(f'- NEAR <=1.5: 1m price >= {NEAR_PX_MIN:.2f}% AND 6m extension < {NEAR_EXTENSION_MAX:.1f}%')
    print(f'- MID 1.5-8: p5 >= {MID_P5_MIN:.2f}, p1 >= {MID_P1_MIN:.2f}, 1m price >= {MID_PX_MIN:.2f}%')
    print(f'- BOUNDARY 8-12: MACD >= {BOUNDARY_MACD_MIN:.1f}, RSI >= {BOUNDARY_RSI_MIN:.1f}, 1m price >= {BOUNDARY_PX_MIN:.2f}%')
    print('- DEEP >12: excluded from gradual-turn')

    print('\n=== SELECTED SIGNALS BY REGIME ===')
    print(f"NEAR={regime_counts.get('NEAR_LE1_5', 0)} MID={regime_counts.get('MID_1_5_8', 0)} BOUNDARY={regime_counts.get('BOUNDARY_8_12', 0)} DEEP={regime_counts.get('DEEP_GT12', 0)} TOTAL={len(selected)}")
    print(f'Overlap with V20 signals: {overlap}')

    print('\n=== PERFORMANCE ===')
    cols = ['label','trades','wins','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct','signals','overlaps_v20']
    print(summary[cols].to_string(index=False))

    print('\nInterpretation guardrails:')
    print('- This is the current best structural hypothesis, not a production freeze.')
    print('- MID remains under-sampled in the current history.')
    print('- DEEP remains a separate reversal family and is intentionally excluded.')
    print('- After this run, inspect added winners/losses before OOS/US-market validation.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_SELECTED)
    print('WROTE', OUT_EXTRA_TRADES)
    print('WROTE', OUT_MERGED_TRADES)


if __name__ == '__main__':
    main()
