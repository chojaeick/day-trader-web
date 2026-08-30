from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import inspect
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_v22_early_ratchet_sweep as early
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_early_ratchet_gate_sweep')
FEE = integ.FEE_RT_PCT
WINDOW_MINUTES = 5.0
RATCHET_RATIO = 0.50
GATES = [0.0, 0.5, 1.0, 1.5, 2.0]


def n(x):
    return str(x).zfill(6)


def stats(label, tr):
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - FEE
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    pos = net[net > 0]
    neg = net[net < 0]
    return dict(
        case=label,
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        avg_win_pct=float(pos.mean()) if len(pos) else np.nan,
        avg_loss_pct=float(neg.mean()) if len(neg) else np.nan,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
        max_win_pct=float(net.max()) if len(net) else np.nan,
        net_loss_ge_3_count=int((net <= -3.0).sum()) if len(net) else 0,
        gross_loss_ge_3_count=int((g <= -3.0).sum()) if len(g) else 0,
    )


def build_gated_simulator():
    """Reuse validated causal early-ratchet simulator; add ratio + MFE activation gate only."""
    src = inspect.getsource(early.simulate_variant)
    old_sig = "def simulate_variant(packed, state_events, tagged, ratchet_minutes=None, post_policy=None):"
    new_sig = "def simulate_gated_variant(packed, state_events, tagged, activation_pct, ratchet_ratio=0.50, ratchet_minutes=5.0, post_policy='REVERT'):"
    old_line = "candidate = base_stop + max(0.0, pos['ratchet_peak'] - pos['entry_price'])"
    new_line = "mfe_pct = max(0.0, (pos['ratchet_peak'] / pos['entry_price'] - 1.0) * 100.0)\n                    candidate = (base_stop + float(ratchet_ratio) * max(0.0, pos['ratchet_peak'] - pos['entry_price'])) if mfe_pct >= float(activation_pct) else np.nan"
    if old_sig not in src or old_line not in src:
        raise RuntimeError('Upstream early-ratchet simulator changed; refusing unsafe source rewrite.')
    src = src.replace(old_sig, new_sig, 1).replace(old_line, new_line, 1)
    ns = dict(early.__dict__)
    exec(src, ns)
    return ns['simulate_gated_variant']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR 5M / 50% RATCHET ACTIVATION GATE SWEEP ===', flush=True)
    print('Rule: first 5 minutes only; 50% ratchet; REVERT after 5m.', flush=True)
    print('Activation gates are based only on PRIOR completed-bar MFE: 0/0.5/1.0/1.5/2.0%.', flush=True)
    print('Risk columns count both gross <= -3% and fee-adjusted net <= -3%.', flush=True)

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
    tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)

    baseline = integ.simulate(packed, states, tagged)
    b = stats('A', baseline)
    print('A', b, flush=True)

    simulate_gated = build_gated_simulator()
    rows = [b]
    alltr = []

    xb = baseline.copy()
    xb['case'] = 'A'
    xb['activation_pct'] = np.nan
    xb['ratchet_ratio'] = 0.0
    xb['ratchet_exit'] = False
    xb['ratchet_peak_pct'] = np.nan
    xb['ratchet_stop_pct'] = np.nan
    alltr.append(xb)

    for gate in GATES:
        name = f'G{int(round(gate * 100)):03d}_R50_5M_REVERT'
        tr = simulate_gated(
            packed,
            states,
            tagged,
            activation_pct=gate,
            ratchet_ratio=RATCHET_RATIO,
            ratchet_minutes=WINDOW_MINUTES,
            post_policy='REVERT',
        )
        st = stats(name, tr)
        rows.append(st)
        x = tr.copy()
        x['case'] = name
        x['activation_pct'] = gate
        x['ratchet_ratio'] = RATCHET_RATIO
        alltr.append(x)
        print(name, st, flush=True)

    summary = pd.DataFrame(rows)
    trades = pd.concat(alltr, ignore_index=True, sort=False)

    # Gate 0 must exactly reproduce prior 50% / 5m / REVERT result.
    g0 = summary[summary.case == 'G000_R50_5M_REVERT'].iloc[0]
    expected_net = 39.30526710762962
    expected_trades = 44
    tol = 1e-9
    guard = int(g0.trades) == expected_trades and abs(float(g0.net_sum_pct) - expected_net) < tol
    print('\nREPRO CHECK G0 == PRIOR R5_050_REVERT:', 'PASS' if guard else 'FAIL')
    if not guard:
        raise SystemExit('G0 mismatch; activation-gate sweep invalid.')

    summary.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    q = trades[(trades['case'] != 'A') & trades['ratchet_exit'].fillna(False)]
    print('\n=== RATCHET EXIT COUNTS ===')
    names = [f'G{int(round(g * 100)):03d}_R50_5M_REVERT' for g in GATES]
    print(q.groupby('case').size().reindex(names, fill_value=0).to_string())

    print('\n=== RATCHET EXITS BY SOURCE ===')
    print(q.groupby(['case', 'source']).size().to_string() if len(q) else 'NONE')

    print('\n=== LOSSES <= -3% NET ===')
    risk = trades.copy()
    risk['net_pct'] = pd.to_numeric(risk.pnl_pct, errors='coerce') - FEE
    z = risk[risk.net_pct <= -3.0]
    cols = ['case','source','symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','net_pct','reason']
    print(z[cols].sort_values(['case','net_pct']).to_string(index=False) if len(z) else 'NONE')

    print('\n=== TP1 DONE COUNTS ===')
    print(trades.groupby('case').tp1_done.sum().to_string())

    print('\n=== RATCHET EXIT DETAILS ===')
    cols = ['case','source','symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','ratchet_peak_pct','ratchet_stop_pct']
    print(q[cols].sort_values(['case','entry_time']).to_string(index=False) if len(q) else 'NONE')

    print('WROTE', OUT / 'summary.csv')
    print('WROTE', OUT / 'trades.csv')


if __name__ == '__main__':
    main()
