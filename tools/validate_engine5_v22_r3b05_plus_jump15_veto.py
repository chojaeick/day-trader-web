from __future__ import annotations

"""KR V22 combined timing diagnostic.

Combine the best early-entry timing candidate with the prior 15-point last-1m
entry veto:

  1) R3_B05 early entry:
     - three consecutive positive 1m live-score rises into T-1
     - last completed rise into T-1 < 20
     - live_score(T-1) + 5 >= 50
     - if eligible, advance the existing tagged signal from T to T-1 using only
       provisional geometry available at T-1

  2) Jump-15 veto for signals that were NOT advanced:
     - at the normal entry time T, if live_score(T)-live_score(T-1) >= 15,
       veto that normal entry.

This ordering matters: an entry that is causally eligible one minute earlier is
executed earlier and is not later cancelled using the future T score. Remaining
normal-time entries are evaluated with the 15-point jump veto at T.

This is still anchored to the later existing tagged cohort, so it remains a
timing diagnostic rather than a deployable upstream production rule.
"""

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
import tools.validate_engine5_v22_consecutive_rise_merit_early_entry as merit
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_r3b05_plus_jump15_veto')
FEE = integ.FEE_RT_PCT
JUMP_VETO = 15.0


def n(x): return str(x).zfill(6)


def finite(x):
    try:
        z = float(x)
        return z if np.isfinite(z) else np.nan
    except Exception:
        return np.nan


def stats(label, tr):
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - FEE
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        case=label,
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
        max_win_pct=float(net.max()) if len(net) else np.nan,
    )


def live_score(cache, raw, sym, ts, cfg):
    key = (sym, pd.Timestamp(ts))
    if key not in cache:
        r = diag.score_at(raw[sym], pd.Timestamp(ts), cfg)
        cache[key] = np.nan if r is None else finite(r.get('live_score', np.nan))
    return cache[key]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR R3_B05 + JUMP>=15 NORMAL-ENTRY VETO ===', flush=True)
    print('Order: advance R3_B05 at T-1 first; only non-advanced normal T entries face jump>=15 veto.', flush=True)
    print('No future T score is used to cancel an entry already advanced at T-1.', flush=True)

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
    guard = int(b['trades']) == 44 and abs(float(b['net_sum_pct']) - 46.35511700526944) < 1e-6
    print('BASELINE', b)
    print('BASELINE REPRO:', 'PASS' if guard else 'FAIL')
    if not guard:
        raise SystemExit('Baseline mismatch; combined validation invalid.')

    score_cache = {}

    # Reuse the already validated R3 merit timing implementation with fixed +5.
    early_tags, changes = merit.build_case(
        tagged=tagged,
        raw=raw,
        cfg=cfg,
        required_rises=3,
        merit=5.0,
        score_cache=score_cache,
    )
    r3 = integ.simulate(packed, states, early_tags)
    r3_stats = stats('R3_B05', r3)
    r3_stats['advanced_tags'] = len(changes)

    combined = []
    veto_rows = []
    for item in early_tags:
        sym = n(item['symbol'])
        ts = pd.Timestamp(item['time'])

        # Advanced T-1 events bypass the later T veto because the later score is
        # not observable yet at the actual earlier decision point.
        is_advanced = False
        if len(changes):
            q = changes[
                (changes.symbol.astype(str).str.zfill(6) == sym) &
                (pd.to_datetime(changes.early_time) == ts) &
                (changes.source.astype(str) == str(item['source']))
            ]
            is_advanced = not q.empty
        if is_advanced:
            combined.append(item)
            continue

        s0 = live_score(score_cache, raw, sym, ts, cfg)
        s1 = live_score(score_cache, raw, sym, ts - pd.Timedelta(minutes=1), cfg)
        jump = s0 - s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
        if np.isfinite(jump) and jump >= JUMP_VETO:
            veto_rows.append(dict(
                symbol=sym, source=item['source'], time=ts,
                live_score_t_1=s1, live_score_t=s0, last_1m_jump=jump,
                event_score=float(item['event'][2]),
            ))
            continue
        combined.append(item)

    combo = integ.simulate(packed, states, combined)
    cstats = stats('R3_B05_PLUS_VETO15', combo)
    cstats['advanced_tags'] = len(changes)
    cstats['vetoed_remaining_tags'] = len(veto_rows)

    rows = [b, r3_stats, cstats]
    summary = pd.DataFrame(rows)
    vetoes = pd.DataFrame(veto_rows)

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== R3_B05 ADVANCED TAGS ===')
    print(changes.to_string(index=False) if len(changes) else 'NONE')

    print('\n=== REMAINING NORMAL-TIME ENTRIES VETOED BY JUMP>=15 ===')
    print(vetoes.to_string(index=False) if len(vetoes) else 'NONE')

    if len(vetoes):
        be = baseline[['symbol','entry_time','pnl_pct','reason','source']].copy()
        be['symbol'] = be.symbol.astype(str).str.zfill(6)
        vx = vetoes.merge(
            be,
            left_on=['symbol','time'],
            right_on=['symbol','entry_time'],
            how='left',
            suffixes=('','_baseline'),
        )
        print('\n=== VETO15 MATCHED TO BASELINE EXECUTED TRADES ===')
        print(vx.to_string(index=False))
        vx.to_csv(OUT/'veto15_matched_baseline.csv', index=False)

    parts = []
    for name, tr in [('A', baseline), ('R3_B05', r3), ('R3_B05_PLUS_VETO15', combo)]:
        q = tr.copy(); q['case'] = name; parts.append(q)
    trades = pd.concat(parts, ignore_index=True, sort=False)

    summary.to_csv(OUT/'summary.csv', index=False)
    trades.to_csv(OUT/'trades.csv', index=False)
    changes.to_csv(OUT/'advanced_tags.csv', index=False)
    vetoes.to_csv(OUT/'vetoed_remaining_normal_entries.csv', index=False)
    print('\nWROTE', OUT/'summary.csv')
    print('WROTE', OUT/'trades.csv')
    print('WROTE', OUT/'advanced_tags.csv')
    print('WROTE', OUT/'vetoed_remaining_normal_entries.csv')


if __name__ == '__main__':
    main()
