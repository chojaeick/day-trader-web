from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.diagnose_engine5_v22_preentry_minute_scores as diag
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_late_score_spike_veto')
FEE = integ.FEE_RT_PCT
THRESHOLDS = [15.0, 20.0, 25.0, 30.0]


def n(x): return str(x).zfill(6)


def stats(label, tr):
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - FEE
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(case=label, trades=len(net), wins=int((net > 0).sum()),
                win_pct=float((net > 0).mean()*100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                avg_net_pct=float(net.mean()) if len(net) else 0.0,
                pf=(gp/gl if gl > 0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan,
                max_win_pct=float(net.max()) if len(net) else np.nan)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR LAST-1M LIVE-SCORE SPIKE VETO ===', flush=True)
    print('Cases: A baseline and veto when causal live score jump T-1 -> T >= 15/20/25/30.', flush=True)
    print('This is a diagnostic filter on the existing tagged entry cohort; no production logic is changed.', flush=True)

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

    score_cache = {}
    def live_score(sym, ts):
        key = (sym, pd.Timestamp(ts))
        if key not in score_cache:
            r = diag.score_at(raw[sym], pd.Timestamp(ts), cfg)
            score_cache[key] = np.nan if r is None else float(r['live_score'])
        return score_cache[key]

    annotated = []
    for item in tagged:
        sym = n(item['symbol']); ts = pd.Timestamp(item['time'])
        s0 = live_score(sym, ts)
        s1 = live_score(sym, ts - pd.Timedelta(minutes=1))
        jump = s0 - s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
        x = dict(item)
        x['live_score_t'] = s0
        x['live_score_t_1'] = s1
        x['last_1m_jump'] = jump
        annotated.append(x)

    baseline = integ.simulate(packed, states, tagged)
    rows = [stats('A', baseline)]
    alltr = []
    xb = baseline.copy(); xb['case'] = 'A'; alltr.append(xb)
    veto_rows = []

    for th in THRESHOLDS:
        kept = []
        vetoed = []
        for x in annotated:
            jump = x['last_1m_jump']
            if np.isfinite(jump) and jump >= th:
                vetoed.append(x)
            else:
                kept.append(x)
        tr = integ.simulate(packed, states, kept)
        name = f'VETO_JUMP_GE_{int(th)}'
        rows.append(stats(name, tr))
        xt = tr.copy(); xt['case'] = name; alltr.append(xt)
        for x in vetoed:
            veto_rows.append(dict(case=name, symbol=x['symbol'], source=x['source'], time=x['time'],
                                  live_score_t_1=x['live_score_t_1'], live_score_t=x['live_score_t'],
                                  last_1m_jump=x['last_1m_jump'], event_score=float(x['event'][2])))
        print(name, rows[-1], 'tagged_vetoes=', len(vetoed), flush=True)

    summary = pd.DataFrame(rows)
    trades = pd.concat(alltr, ignore_index=True, sort=False)
    vetoes = pd.DataFrame(veto_rows)

    b = summary.iloc[0]
    guard = int(b.trades) == 44 and abs(float(b.net_sum_pct) - 46.355117) < 1e-5
    print('\nBASELINE REPRO:', 'PASS' if guard else 'FAIL', dict(b))
    if not guard:
        raise SystemExit('Baseline mismatch; veto sweep invalid.')

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== VETOED TAGGED CANDIDATES ===')
    if len(vetoes): print(vetoes.sort_values(['case','time','symbol']).to_string(index=False))
    else: print('NONE')

    be = baseline[['symbol','entry_time','pnl_pct','reason','source']].copy()
    be['symbol'] = be.symbol.astype(str).str.zfill(6)
    if len(vetoes):
        vx = vetoes.merge(be, left_on=['symbol','time'], right_on=['symbol','entry_time'], how='left', suffixes=('','_baseline'))
        print('\n=== VETOES MATCHED TO BASELINE EXECUTED TRADES ===')
        print(vx.to_string(index=False))
        vx.to_csv(OUT / 'vetoes_matched_baseline.csv', index=False)

    summary.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)
    vetoes.to_csv(OUT / 'vetoed_candidates.csv', index=False)
    print('\nWROTE', OUT / 'summary.csv')
    print('WROTE', OUT / 'trades.csv')
    print('WROTE', OUT / 'vetoed_candidates.csv')

if __name__ == '__main__':
    main()
