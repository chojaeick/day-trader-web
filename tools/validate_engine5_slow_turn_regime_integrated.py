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
import tools.diagnose_v20_transition_structure_targets as st
import tools.diagnose_engine5_slow_turn_zero_cross_distance as zd
import tools.validate_engine5_slow_turn_prototype as slow
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
PERSIST_SRC = OUT_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = OUT_DIR / 'slow_turn_regime_integrated_summary.csv'
OUT_SELECTED = OUT_DIR / 'slow_turn_regime_integrated_selected.csv'
THRESHOLD = 50
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


def strength_ok(r):
    return finite(r.gap_delta_5m) >= 30.0 and finite(r.rsi_slope_5m) >= 10.0


def common_regime_ok(r):
    z = finite(r.zero_cross_bars)
    p5 = finite(r.joint5_persistence)
    p1 = finite(r.joint1_persistence)
    px = finite(r.price_progress_1m_pct)

    if not np.isfinite(z) or not np.isfinite(px):
        return False, 'INVALID'

    if z <= 1.5:
        return px >= 0.75, 'NEAR_LE1_5'

    if z <= 8.0:
        ok = p5 >= 0.60 and p1 >= 0.60 and px >= 1.0
        return ok, 'MID_1_5_8'

    if z <= 12.0:
        ok = strength_ok(r) and px >= 1.5
        return ok, 'BOUNDARY_8_12'

    return True, 'DEEP_GT12'


def deep_ok(r, policy):
    p5 = finite(r.joint5_persistence)
    p1 = finite(r.joint1_persistence)
    px = finite(r.price_progress_1m_pct)

    if policy == 'NO_DEEP':
        return False
    if policy == 'DEEP_60_60_PX075':
        return p5 >= 0.60 and p1 >= 0.60 and px >= 0.75
    if policy == 'DEEP_80_70_PX075':
        return p5 >= 0.80 and p1 >= 0.70 and px >= 0.75
    if policy == 'DEEP_80_70_PX100':
        return p5 >= 0.80 and p1 >= 0.70 and px >= 1.00
    raise ValueError(policy)


def select_policy(df, policy):
    keep = []
    regimes = []
    for _, r in df.iterrows():
        ok, regime = common_regime_ok(r)
        if regime == 'DEEP_GT12':
            ok = deep_ok(r, policy)
        keep.append(bool(ok))
        regimes.append(regime)
    out = df.copy()
    out['regime'] = regimes
    return out[np.asarray(keep, dtype=bool)].copy()


def reconstruct_base_candidates(raw, cfg, scored, completed, micros):
    parts = []
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        q = zd.build_candidates(s, pf, micros[s], scored[s])
        if len(q):
            parts.append(q)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


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

    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength, raw_min=52.0, rel_min=1.45)
    v20_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)
    v20_stat = stat('V20_FROZEN', v20_trades)

    print('=== SLOW TURN REGIME-INTEGRATED VALIDATION ===')
    print('V20 is frozen. No V20 rule changed.')
    print(pd.DataFrame([v20_stat]).to_string(index=False))

    base_cand = reconstruct_base_candidates(raw, cfg, scored, completed, micros)
    base_cand['symbol'] = base_cand['symbol'].astype(str).str.zfill(6)
    base_cand['entry_time'] = pd.to_datetime(base_cand['entry_time'])

    diag = pd.read_csv(PERSIST_SRC)
    diag['symbol'] = diag['symbol'].astype(str).str.zfill(6)
    diag['entry_time'] = pd.to_datetime(diag['entry_time'])

    # zero_cross_bars already exists on the reconstructed candidate rows. Do not
    # merge the same-named diagnostic copy, otherwise pandas suffixes it to _x/_y.
    cols = [
        'symbol','entry_time','joint5_persistence','joint1_persistence',
        'price_progress_1m_pct'
    ]
    x = base_cand.merge(diag[cols], on=['symbol','entry_time'], how='inner', validate='one_to_one')

    if len(x) != len(base_cand):
        print(f'WARNING candidate join: reconstructed={len(base_cand)} joined={len(x)}')

    policies = ['NO_DEEP','DEEP_60_60_PX075','DEEP_80_70_PX075','DEEP_80_70_PX100']
    v20_keys = {(pd.Timestamp(ts), n(c[0])) for ts, cs in ev20.items() for c in cs}

    rows = []
    selected_rows = []
    for policy in policies:
        sel = select_policy(x, policy)
        sev = zd.event_stream(sel)
        extra_trades = multi.simulate_multi(packed, sev, states, THRESHOLD)
        merged_trades = multi.simulate_multi(packed, slow.merge_streams(ev20, sev), states, THRESHOLD)

        se = stat('EXTRA', extra_trades)
        sm = stat('MERGED', merged_trades)
        overlap = sum((pd.Timestamp(r.entry_time), n(r.symbol)) in v20_keys for _, r in sel.iterrows())

        rc = sel['regime'].value_counts().to_dict() if len(sel) else {}
        rows.append(dict(
            policy=policy,
            signals=len(sel), overlaps_v20=overlap,
            near_le1_5=rc.get('NEAR_LE1_5', 0),
            mid_1_5_8=rc.get('MID_1_5_8', 0),
            boundary_8_12=rc.get('BOUNDARY_8_12', 0),
            deep_gt12=rc.get('DEEP_GT12', 0),
            extra_trades=se['trades'], extra_wins=se['wins'], extra_win_pct=se['win_pct'],
            extra_net=se['net_sum_pct'], extra_pf=se['pf'], extra_max_loss=se['max_loss_pct'],
            merged_trades=sm['trades'], merged_wins=sm['wins'], merged_win_pct=sm['win_pct'],
            merged_net=sm['net_sum_pct'], merged_pf=sm['pf'], merged_max_loss=sm['max_loss_pct'],
        ))

        if len(sel):
            q = sel.drop(columns=['event'], errors='ignore').copy()
            q['policy'] = policy
            selected_rows.append(q)

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_SUMMARY, index=False)
    if selected_rows:
        pd.concat(selected_rows, ignore_index=True).to_csv(OUT_SELECTED, index=False)

    print('\n=== INTEGRATED POLICY SUMMARY ===')
    print(summary.to_string(index=False))
    print('\nExpected frozen regression: V20 39 trades / 17 wins / +20.012131% / PF 1.86292.')
    print('Do not select a policy from tiny samples automatically; compare robustness and added-trade quality.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_SELECTED)


if __name__ == '__main__':
    main()
