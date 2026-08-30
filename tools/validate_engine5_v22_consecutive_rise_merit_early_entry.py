from __future__ import annotations

"""KR V22 timing diagnostic for consecutive-rise merit early entry.

Purpose
-------
Test the user's rule directly: if the causal provisional entry score has risen
consecutively for 2 or 3 one-minute steps, grant a small merit bonus so the
signal may reach the normal 50-point entry threshold one minute earlier.

This is still a timing diagnostic because candidate identity/source is anchored
by the later existing V22 tagged event at T. It does NOT modify production logic.
If a rule survives this diagnostic, rebuild it causally upstream before deployment.

Cases
-----
A              : current V22 baseline
R2_B05 / B10   : >=2 consecutive rises into T-1, +5 / +10 merit
R3_B05/B10/B15 : >=3 consecutive rises into T-1, +5 / +10 / +15 merit

Safety
------
- merit is fixed, not proportional to the last jump
- every required step must be strictly positive
- the most recent step must be < 20 points to avoid the late-spike pattern
- effective_score = live_score(T-1) + merit
- early entry occurs only if effective_score >= 50
- V_REBOUND is left unchanged because its structural stop belongs to its
  source-specific later state
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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_consecutive_rise_merit_early_entry')
FEE = integ.FEE_RT_PCT
ENTRY_THRESHOLD = 50.0
LATE_SPIKE_VETO = 20.0
CASES = [
    ('R2_B05', 2, 5.0),
    ('R2_B10', 2, 10.0),
    ('R3_B05', 3, 5.0),
    ('R3_B10', 3, 10.0),
    ('R3_B15', 3, 15.0),
]


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


def provisional_row(bars, ts, cfg):
    p5 = diag.provisional_5m(bars, pd.Timestamp(ts))
    if len(p5) < max(30, int(cfg.bb_period) + 5):
        return None
    eng = DoubleBollingerEngine5(cfg)
    f = v10._refine_entry_frame(eng.enrich(p5))
    s = reweight({'X': f}, cfg, 0.0)['X']
    return None if s.empty else s.iloc[-1]


def event_from_provisional(sym, ts, cfg, bars, effective_score, original_event):
    r = provisional_row(bars, ts, cfg)
    if r is None:
        return None
    iu = finite(r.get('inner_upper', np.nan)); il = finite(r.get('inner_lower', np.nan))
    ou = finite(r.get('outer_upper', np.nan)); mid = finite(r.get('mid', np.nan))
    close = finite(r.get('close', np.nan))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not (np.isfinite(close) and np.isfinite(band_r) and band_r > 0):
        return None
    msv = finite(r.get('macd_slope_spread_strength', np.nan))
    rsv = finite(r.get('rsi_slope_strength', np.nan))
    extended = bool(np.isfinite(ou) and close > ou)
    breakout = bool(original_event[-1]) if len(original_event) >= 13 else False
    return (n(sym), close, float(effective_score), msv, rsv,
            band_r, band_r, iu, il, ou, mid, extended, breakout)


def score_at_cached(cache, raw, sym, ts, cfg):
    key = (sym, pd.Timestamp(ts))
    if key not in cache:
        r = diag.score_at(raw[sym], pd.Timestamp(ts), cfg)
        cache[key] = np.nan if r is None else finite(r.get('live_score', np.nan))
    return cache[key]


def build_case(tagged, raw, cfg, required_rises, merit, score_cache):
    out, changes = [], []
    for item in tagged:
        if item['source'] == 'V_REBOUND':
            out.append(item)
            continue

        sym = n(item['symbol']); t = pd.Timestamp(item['time'])
        # To enter at T-1 using only information then available, inspect the score
        # path ending at T-1. R2 uses T-3,T-2,T-1. R3 uses T-4..T-1.
        offsets = list(range(required_rises + 1, 0, -1))
        times = [t - pd.Timedelta(minutes=k) for k in offsets]
        scores = [score_at_cached(score_cache, raw, sym, ts, cfg) for ts in times]
        valid = all(np.isfinite(x) for x in scores)
        deltas = [scores[i+1] - scores[i] for i in range(len(scores)-1)] if valid else []
        consecutive = bool(valid and len(deltas) == required_rises and all(d > 0.0 for d in deltas))
        last_step = deltas[-1] if deltas else np.nan
        live_t1 = scores[-1] if valid else np.nan
        effective = live_t1 + float(merit) if valid else np.nan
        eligible = bool(consecutive and last_step < LATE_SPIKE_VETO and effective >= ENTRY_THRESHOLD)

        if eligible:
            nt = t - pd.Timedelta(minutes=1)
            ev = event_from_provisional(sym, nt, cfg, raw[sym], effective, item['event'])
            if ev is not None:
                x = dict(item); x['time'] = nt; x['event'] = ev
                out.append(x)
                rec = dict(
                    symbol=sym, source=item['source'], original_time=t, early_time=nt,
                    required_rises=required_rises, merit=merit,
                    live_score_t_1=live_t1, effective_score=effective,
                    last_step=last_step,
                    original_event_score=float(item['event'][2]),
                    original_price=float(item['event'][1]), early_price=float(ev[1]),
                    price_improvement_pct=(float(item['event'][1]) / float(ev[1]) - 1.0) * 100.0,
                )
                for j, sc in enumerate(scores):
                    rec[f'score_path_{j}'] = sc
                for j, d in enumerate(deltas):
                    rec[f'delta_{j}'] = d
                changes.append(rec)
                continue
        out.append(item)

    out = sorted(out, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
    return out, pd.DataFrame(changes)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR CONSECUTIVE-RISE MERIT EARLY ENTRY ===', flush=True)
    print('2 or 3 consecutive positive 1m live-score rises may earn fixed merit and enter at T-1.', flush=True)
    print('Merit cases: R2 +5/+10, R3 +5/+10/+15. Last step >=20 gets no early entry.', flush=True)
    print('Timing diagnostic only; production logic is unchanged.', flush=True)

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
        raise SystemExit('Baseline mismatch; merit early-entry validation invalid.')

    score_cache = {}
    summary_rows = [dict(**b, advanced_tags=0, executed_advanced=0,
                         avg_price_improvement_pct=np.nan, median_price_improvement_pct=np.nan)]
    trade_parts = [baseline.assign(case='A')]
    change_parts = []

    for name, rises, merit in CASES:
        tags, changes = build_case(tagged, raw, cfg, rises, merit, score_cache)
        tr = integ.simulate(packed, states, tags)
        st = stats(name, tr)

        executed_advanced = 0
        if len(changes) and len(tr):
            ek = set(zip(tr.symbol.astype(str).str.zfill(6), pd.to_datetime(tr.entry_time)))
            executed_advanced = sum((r.symbol, pd.Timestamp(r.early_time)) in ek for r in changes.itertuples(index=False))

        st.update(
            advanced_tags=len(changes),
            executed_advanced=int(executed_advanced),
            avg_price_improvement_pct=float(changes.price_improvement_pct.mean()) if len(changes) else np.nan,
            median_price_improvement_pct=float(changes.price_improvement_pct.median()) if len(changes) else np.nan,
        )
        summary_rows.append(st)
        trade_parts.append(tr.assign(case=name))
        if len(changes):
            changes['case'] = name
            change_parts.append(changes)
        print(name, st, flush=True)

    summary = pd.DataFrame(summary_rows)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    changes = pd.concat(change_parts, ignore_index=True, sort=False) if change_parts else pd.DataFrame()

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== EARLY ENTRY CHANGES ===')
    if len(changes):
        show = [c for c in [
            'case','symbol','source','original_time','early_time','required_rises','merit',
            'live_score_t_1','effective_score','last_step','original_price','early_price',
            'price_improvement_pct'
        ] if c in changes.columns]
        print(changes[show].sort_values(['case','early_time','symbol']).to_string(index=False))
    else:
        print('NONE')

    if len(changes):
        be = baseline[['symbol','entry_time','entry_price','pnl_pct','reason','source']].copy()
        be['symbol'] = be.symbol.astype(str).str.zfill(6)
        matches = []
        for case, q in changes.groupby('case'):
            ct = trades[trades.case == case].copy()
            ct['symbol'] = ct.symbol.astype(str).str.zfill(6)
            z = q.merge(be, left_on=['symbol','original_time'], right_on=['symbol','entry_time'], how='left', suffixes=('','_baseline'))
            z = z.merge(
                ct[['symbol','entry_time','entry_price','pnl_pct','reason']],
                left_on=['symbol','early_time'], right_on=['symbol','entry_time'], how='left',
                suffixes=('_baseline','_early')
            )
            if 'pnl_pct_baseline' in z.columns and 'pnl_pct_early' in z.columns:
                z['gross_pnl_delta_pct'] = pd.to_numeric(z.pnl_pct_early, errors='coerce') - pd.to_numeric(z.pnl_pct_baseline, errors='coerce')
            matches.append(z)
        matched = pd.concat(matches, ignore_index=True, sort=False)
        print('\n=== ADVANCED SIGNAL OUTCOME MATCH ===')
        cols = [c for c in [
            'case','symbol','source','original_time','early_time','live_score_t_1','effective_score',
            'price_improvement_pct','pnl_pct_baseline','pnl_pct_early','gross_pnl_delta_pct',
            'reason_baseline','reason_early'
        ] if c in matched.columns]
        print(matched[cols].to_string(index=False))
        matched.to_csv(OUT / 'advanced_signal_outcomes.csv', index=False)

    summary.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)
    changes.to_csv(OUT / 'advanced_tags.csv', index=False)
    print('\nWROTE', OUT / 'summary.csv')
    print('WROTE', OUT / 'trades.csv')
    print('WROTE', OUT / 'advanced_tags.csv')


if __name__ == '__main__':
    main()
