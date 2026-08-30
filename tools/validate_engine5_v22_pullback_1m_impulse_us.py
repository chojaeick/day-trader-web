from __future__ import annotations

"""US validation for V22 pullback re-entry with strong completed 1m impulse.

Baseline is the already-finalized US V22 timing policy:
  corrected slow-turn dual gate 55 + R3_B05 early entry + remaining jump>=15 veto.

Then add an independent pullback re-entry watch after either:
  1) an actually realized losing V22 trade, or
  2) a normal-time primary candidate rejected by the finalized jump>=15 veto.

Re-entry trigger is intentionally simple because the 5m trend is already established:
  - 5m trend_up alive and mid_slope8 > 0
  - pullback occurred and higher-low remains above pre-arm 10m structural low
  - MACD slope > 0 and RSI slope > 0
  - completed 1m candle is green
  - close-to-close 1m impulse >= 0.7% or 1.0%
  - pullback score >= 65 (same diagnostic score interface)

No pullback-specific 5m stop ratchet is applied. Existing Engine5 risk/exit geometry is preserved.
Native USD / original ET fresh map only. Diagnostic; production V22 is unchanged.
"""

import pickle
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_v22_late_score_spike_veto_us as usv
import tools.validate_engine5_v22_r3b05_plus_jump_veto_us as finalus
import tools.validate_engine5_v22_pullback_1m_impulse_sweep as imp
import tools.validate_engine5_v22_uptrend_pullback_reentry as pb

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_pullback_1m_impulse_us')
IMPULSE_PCTS = [0.7, 1.0]
FEE = 0.25
JUMP_VETO = 15.0
GROUPS = {
    'VETO15_ONLY': ['VETO15'],
    'LOSING_EXIT_ONLY': ['LOSING_EXIT'],
    'BOTH': ['VETO15', 'LOSING_EXIT'],
}


def n(x):
    return str(x).zfill(6)


def finite(x):
    try:
        z = float(x)
        return z if np.isfinite(z) else np.nan
    except Exception:
        return np.nan


def metrics(label, trades):
    gross = pd.to_numeric(trades.get('pnl_pct'), errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    net = gross - FEE
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
        net_loss_ge_3_count=int((net <= -3.0).sum()) if len(net) else 0,
    )


def finalized_v22_tags(raw, cfg, completed, micros, old_tags):
    """Reproduce finalized US V22 tag stream and return vetoed normal-T candidates."""
    corrected = usv.build_corrected_55_tags(raw, cfg, completed, micros, old_tags)
    score_cache = {}
    early_tags, changes = finalus.build_r3_b05(corrected, raw, cfg, score_cache)

    early_keys = set()
    if len(changes):
        early_keys = set((n(r.symbol), pd.Timestamp(r.early_time), str(r.source))
                         for r in changes.itertuples(index=False))

    kept, vetoed = [], []
    for item in early_tags:
        sym = n(item['symbol'])
        ts = pd.Timestamp(item['time'])
        src = str(item['source'])
        if (sym, ts, src) in early_keys:
            kept.append(item)
            continue
        s0 = finalus.score_at_cached(score_cache, raw, sym, ts, cfg)
        s1 = finalus.score_at_cached(score_cache, raw, sym, ts - pd.Timedelta(minutes=1), cfg)
        jump = s0 - s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
        if np.isfinite(jump) and jump >= JUMP_VETO:
            vetoed.append(dict(
                symbol=sym,
                arm_time=ts,
                arm_reason='VETO15',
                primary_source=src,
                primary_time=ts,
                primary_jump=float(jump),
            ))
        else:
            kept.append(item)

    kept = sorted(kept, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
    return kept, pd.DataFrame(vetoed), changes


def build_arms(final_trades, veto_arms):
    rows = []
    if len(veto_arms):
        rows.extend(veto_arms.to_dict('records'))
    for tr in final_trades.itertuples(index=False):
        net = float(tr.pnl_pct) - FEE
        if net <= 0.0:
            rows.append(dict(
                symbol=n(tr.symbol),
                arm_time=pd.Timestamp(tr.exit_time),
                arm_reason='LOSING_EXIT',
                primary_source=str(tr.source),
                primary_time=pd.Timestamp(tr.entry_time),
                primary_jump=np.nan,
            ))
    a = pd.DataFrame(rows)
    if a.empty:
        return a
    return (a.sort_values(['symbol','arm_time','arm_reason'])
             .drop_duplicates(['symbol','arm_time','arm_reason'])
             .reset_index(drop=True))


def make_extra_tags(q):
    tags = []
    for r in q.itertuples(index=False):
        ev = pb.event_from_candidate(r)
        if ev is None:
            continue
        tags.append(dict(
            source='UPTREND_PULLBACK_1M_IMPULSE',
            symbol=n(r.symbol),
            time=pd.Timestamp(r.candidate_time),
            event=ev,
            meta={
                'arm_reason': str(r.arm_reason),
                'arm_time': pd.Timestamp(r.arm_time),
                'primary_time': pd.Timestamp(r.primary_time),
                'impulse_pct': float(r.impulse_pct),
                'impulse_threshold_pct': float(r.impulse_threshold_pct),
            },
        ))
    return tags


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not MAP.exists():
        raise FileNotFoundError(MAP)

    usv.e.apply_us_session_clock()
    with MAP.open('rb') as fh:
        m = pickle.load(fh)
    if m.get('price_unit') != 'USD' or int(m.get('time_shift_minutes', 999)) != 0:
        raise SystemExit('BAD FRESH MAP SEMANTICS: expected native USD / original ET')

    raw = {n(k): v for k, v in m['raw'].items()}
    cfg = m['cfg']
    completed = {n(k): v for k, v in m['completed'].items()}
    micros = {n(k): v for k, v in m['micros'].items()}
    old_tags = list(m['tags'])
    packed = v8.base.pack_exit_events(raw, cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg))

    print('=== V22 US PULLBACK 1M IMPULSE VALIDATION ===', flush=True)
    print('Baseline = corrected55 + R3_B05 + remaining JUMP>=15 veto', flush=True)
    print('Native USD / original ET fresh map', flush=True)
    print('symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()), flush=True)
    print('impulse thresholds=', IMPULSE_PCTS, flush=True)
    print('No pullback-specific 5m ratchet.', flush=True)

    final_tags, veto_arms, changes = finalized_v22_tags(raw, cfg, completed, micros, old_tags)
    baseline = integ.simulate(packed, states, final_tags)
    bm = metrics('V22_FINAL_US_BASELINE', baseline)
    print('\nBASELINE', bm, flush=True)
    print('R3 advanced tags=', len(changes), 'VETO15 arms=', len(veto_arms), flush=True)

    # Known reproduction fingerprint from the finalized-US diagnostic.
    guard = (bm['trades'] == 104 and bm['wins'] == 45 and
             abs(bm['net_sum_pct'] - 4.08) < 0.5 and bm['pf'] > 1.0)
    print('FINAL V22 BASELINE REPRO:', 'PASS' if guard else 'CHECK', flush=True)

    arms_all = build_arms(baseline, veto_arms)
    print('ALL ARMS', len(arms_all),
          arms_all.arm_reason.value_counts().to_dict() if len(arms_all) else {}, flush=True)

    summaries = [bm]
    cand_parts = []
    trade_parts = [baseline.assign(case='V22_FINAL_US_BASELINE')]

    for gname, reasons in GROUPS.items():
        arms = arms_all[arms_all.arm_reason.isin(reasons)].copy().reset_index(drop=True)
        print(f'\n=== {gname} ===', flush=True)
        print('arms=', len(arms), flush=True)
        for threshold in IMPULSE_PCTS:
            q = imp.find_first_impulse_candidates(raw, cfg, arms, threshold)
            if len(q):
                q = (q.sort_values(['candidate_time','impulse_pct'], ascending=[True,False])
                       .drop_duplicates(['symbol','candidate_time']))
                qq = q.copy(); qq['group'] = gname
                cand_parts.append(qq)

            extra = make_extra_tags(q)
            tr = integ.simulate(packed, states, list(final_tags) + extra)
            label = f'{gname}_IMP{str(threshold).replace(".","p")}_US'
            st = metrics(label, tr)
            st['arms'] = len(arms)
            st['selected_candidates'] = len(q)
            st['impulse_threshold_pct'] = threshold
            st['executed_pullback'] = 0
            if len(q) and len(tr):
                ek = set(zip(tr.symbol.astype(str).str.zfill(6), pd.to_datetime(tr.entry_time)))
                st['executed_pullback'] = int(sum((n(r.symbol), pd.Timestamp(r.candidate_time)) in ek
                                                  for r in q.itertuples(index=False)))
            summaries.append(st)
            trade_parts.append(tr.assign(case=label))
            print(label, st, flush=True)

    sdf = pd.DataFrame(summaries)
    print('\n=== SUMMARY ===')
    print(sdf.to_string(index=False))

    if cand_parts:
        candidates = pd.concat(cand_parts, ignore_index=True, sort=False)
        cols = [c for c in ['group','symbol','arm_reason','arm_time','candidate_time','candidate_price',
                            'impulse_threshold_pct','impulse_pct','pullback_score','base_live_score',
                            'macd_slope','rsi_slope','pullback_low','pre_structural_low'] if c in candidates.columns]
        print('\n=== CANDIDATES ===')
        print(candidates[cols].sort_values(['group','impulse_threshold_pct','candidate_time']).to_string(index=False))
        candidates.to_csv(OUT / 'candidates.csv', index=False)
    else:
        candidates = pd.DataFrame()
        print('\nNO PULLBACK CANDIDATES')

    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    sdf.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)
    arms_all.to_csv(OUT / 'arms.csv', index=False)
    veto_arms.to_csv(OUT / 'veto15_arms.csv', index=False)
    print('\nWROTE', OUT, flush=True)


if __name__ == '__main__':
    main()
