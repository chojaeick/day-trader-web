from __future__ import annotations

"""KR each-engine parity validation matching the US standalone harness.

Purpose: apples-to-apples comparison with validate_engine5_us_oos_each_engine.py.
No threshold changes. Uses the same integrated exit simulator and same standalone
source isolation for V20, V-rebound, and revised Slow-turn cuts.
"""

from dataclasses import replace
from pathlib import Path

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUT_SUMMARY = OUT_DIR / 'kr_each_engine_parity_summary.csv'
OUT_TRADES = OUT_DIR / 'kr_each_engine_parity_trades.csv'
OUT_SYMBOLS = OUT_DIR / 'kr_each_engine_parity_by_symbol.csv'
OUT_EXITS = OUT_DIR / 'kr_each_engine_parity_exit_reasons.csv'
CUTS = (-0.15, -0.20, -0.30, -0.50)


def n(x):
    return str(x).zfill(6)


def run_variant(label, tagged, packed, states):
    tr = integ.simulate(packed, states, tagged)
    st = integ.stat(label, tr)
    st['signals'] = len(tagged)
    return tr, st


def main():
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

    print('=== KR EACH-ENGINE PARITY VALIDATION ===', flush=True)
    print('Same source isolation + same exit simulator as US each-engine validator.', flush=True)
    print('NO THRESHOLD CHANGES.', flush=True)

    current = integ.build_sources(raw, cfg, scored, strength, completed, micros)
    v20 = [x for x in current if x['source'] == 'V20']
    vrebound = [x for x in current if x['source'] == 'V_REBOUND']
    print(f'V20 SIGNALS={len(v20)} | V_REBOUND SIGNALS={len(vrebound)}', flush=True)

    print('BUILD LATEST RE-ARMED SLOW-TURN...', flush=True)
    allslow = revised.build_all_slow(raw, cfg, completed, micros)
    print(f'ALL RE-ARMED READY+1M CANDIDATES={len(allslow)}', flush=True)

    variants = [('V20', v20), ('V_REBOUND', vrebound)]
    for cut in CUTS:
        sel = revised.select_revised(allslow, cut)
        variants.append((f'SLOW_TURN_{cut}', revised.slow_tags(sel)))

    summary_rows = []
    trade_parts = []
    symbol_rows = []
    exit_rows = []

    for label, tagged in variants:
        tr, st = run_variant(label, tagged, packed, states)
        summary_rows.append(dict(variant=label, **{k: st[k] for k in
            ['signals','trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']}))
        if len(tr):
            q = tr.copy(); q['variant'] = label; trade_parts.append(q)
            for sym, g in tr.groupby('symbol'):
                s = integ.stat(sym, g)
                symbol_rows.append(dict(variant=label, symbol=sym, trades=s['trades'], wins=s['wins'], losses=s['losses'],
                                        win_pct=s['win_pct'], net_sum_pct=s['net_sum_pct'], avg_net_pct=s['avg_net_pct'],
                                        pf=s['pf'], max_loss_pct=s['max_loss_pct']))
            if 'reason' in tr.columns:
                for reason, count in tr.reason.value_counts().items():
                    exit_rows.append(dict(variant=label, reason=str(reason), count=int(count)))

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(OUT_SUMMARY, index=False)
    pd.DataFrame(symbol_rows).to_csv(OUT_SYMBOLS, index=False)
    pd.DataFrame(exit_rows).to_csv(OUT_EXITS, index=False)
    if trade_parts:
        pd.concat(trade_parts, ignore_index=True).to_csv(OUT_TRADES, index=False)

    print('\n=== KR EACH ENGINE SUMMARY ===')
    print(summary.to_string(index=False, float_format=lambda x: f'{x:.6f}'))

    print('\n=== EXIT REASONS ===')
    ex = pd.DataFrame(exit_rows)
    if len(ex):
        for variant, g in ex.groupby('variant', sort=False):
            print(f'-- {variant} --')
            for _, r in g.iterrows():
                print(f'{r.reason}: {int(r["count"])}')

    print('\nPARITY READING: compare these rows directly with US each-engine summary. Do not compare US standalone Slow-turn with KR integrated total.')
    print('WROTE', OUT_SUMMARY)
    print('WROTE', OUT_TRADES)
    print('WROTE', OUT_SYMBOLS)
    print('WROTE', OUT_EXITS)


if __name__ == '__main__':
    main()
