from __future__ import annotations

"""US timing diagnostic: corrected-55 baseline + R3_B05 early entry + jump veto.

Compare:
  A55                    corrected US dual-gate 55 baseline
  R3_B05                 3 consecutive positive 1m live-score rises into T-1, +5 merit
  R3_B05_PLUS_VETO15     early entries first; remaining normal-T entries vetoed if jump >=15
  R3_B05_PLUS_VETO20     same with jump >=20

Important:
- Native USD / original ET fresh map.
- Candidate cohort is still anchored to the later existing tag, so this is a timing
  diagnostic, not deployable causal upstream production logic.
- V_REBOUND_E is not advanced because its source-specific structural stop belongs
  to the later state.
- An entry already advanced at T-1 is never cancelled with the future T score.
"""

import pickle
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_v22_late_score_spike_veto_us as usv
import tools.validate_engine5_v22_consecutive_rise_merit_early_entry as merit

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_r3b05_plus_jump_veto_us')
FEE = 0.25
ENTRY_THRESHOLD = 50.0
EARLY_LAST_STEP_MAX = 20.0
R3_MERIT = 5.0
VETO_THRESHOLDS = [15.0, 20.0]


def n(x): return str(x).zfill(6)

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
        case=label, trades=len(net), wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
        max_win_pct=float(net.max()) if len(net) else np.nan,
        net_loss_ge_3_count=int((net <= -3.0).sum()) if len(net) else 0,
    )


def score_at_cached(cache, raw, sym, ts, cfg):
    key = (sym, pd.Timestamp(ts))
    if key not in cache:
        r = merit.diag.score_at(raw[sym], pd.Timestamp(ts), cfg)
        cache[key] = np.nan if r is None else finite(r.get('live_score', np.nan))
    return cache[key]


def build_r3_b05(tags, raw, cfg, score_cache):
    out, changes = [], []
    for item in tags:
        # Do not advance rebound source: its structural stop/meta belongs to T.
        if str(item['source']).startswith('V_REBOUND'):
            out.append(item)
            continue

        sym = n(item['symbol']); t = pd.Timestamp(item['time'])
        times = [t - pd.Timedelta(minutes=k) for k in (4, 3, 2, 1)]
        scores = [score_at_cached(score_cache, raw, sym, ts, cfg) for ts in times]
        valid = all(np.isfinite(x) for x in scores)
        deltas = [scores[i+1] - scores[i] for i in range(3)] if valid else []
        consecutive = bool(valid and all(d > 0.0 for d in deltas))
        last_step = deltas[-1] if deltas else np.nan
        live_t1 = scores[-1] if valid else np.nan
        effective = live_t1 + R3_MERIT if valid else np.nan
        eligible = bool(consecutive and last_step < EARLY_LAST_STEP_MAX and effective >= ENTRY_THRESHOLD)

        if eligible:
            nt = t - pd.Timedelta(minutes=1)
            ev = merit.event_from_provisional(sym, nt, cfg, raw[sym], effective, item['event'])
            if ev is not None:
                x = dict(item); x['time'] = nt; x['event'] = ev
                out.append(x)
                rec = dict(
                    symbol=sym, source=item['source'], original_time=t, early_time=nt,
                    live_score_t_1=live_t1, effective_score=effective,
                    last_step=last_step, merit=R3_MERIT,
                    original_event_score=float(item['event'][2]),
                    original_price=float(item['event'][1]), early_price=float(ev[1]),
                    price_improvement_pct=(float(item['event'][1]) / float(ev[1]) - 1.0) * 100.0,
                    score_t_4=scores[0], score_t_3=scores[1], score_t_2=scores[2], score_t_1=scores[3],
                    delta_0=deltas[0], delta_1=deltas[1], delta_2=deltas[2],
                )
                changes.append(rec)
                continue
        out.append(item)

    out = sorted(out, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
    return out, pd.DataFrame(changes)


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

    print('=== US R3_B05 EARLY ENTRY + JUMP VETO | corrected dual-gate 55 ===', flush=True)
    print('Native USD / original ET. Compare A55, R3_B05, +VETO15, +VETO20.', flush=True)
    print('Advanced T-1 entries bypass future T veto. V_REBOUND_E is not advanced.', flush=True)

    tags = usv.build_corrected_55_tags(raw, cfg, completed, micros, old_tags)
    baseline = integ.simulate(packed, states, tags)
    bm = metrics('A55', baseline)
    guard = bm['trades'] == 116 and bm['wins'] == 44 and abs(bm['net_sum_pct'] - (-15.0925)) <= 0.001
    print('BASELINE A55', bm)
    print('A55 REPRO:', 'PASS' if guard else 'FAIL')
    if not guard:
        raise SystemExit('Corrected-55 US baseline mismatch; combined test invalid.')

    score_cache = {}
    early_tags, changes = build_r3_b05(tags, raw, cfg, score_cache)
    r3 = integ.simulate(packed, states, early_tags)
    r3m = metrics('R3_B05', r3)
    r3m['advanced_tags'] = len(changes)
    if len(changes) and len(r3):
        ek = set(zip(r3.symbol.astype(str).str.zfill(6), pd.to_datetime(r3.entry_time)))
        r3m['executed_advanced'] = int(sum((n(r.symbol), pd.Timestamp(r.early_time)) in ek for r in changes.itertuples(index=False)))
    else:
        r3m['executed_advanced'] = 0
    r3m['avg_price_improvement_pct'] = float(changes.price_improvement_pct.mean()) if len(changes) else np.nan

    # Exact keys/times of successfully advanced items. They cannot face a future-T veto.
    early_keys = set()
    if len(changes):
        early_keys = set((n(r.symbol), pd.Timestamp(r.early_time), str(r.source)) for r in changes.itertuples(index=False))

    rows = [bm, r3m]
    trade_parts = [baseline.assign(case='A55'), r3.assign(case='R3_B05')]
    veto_parts = []

    for th in VETO_THRESHOLDS:
        kept, vetoed = [], []
        for item in early_tags:
            sym = n(item['symbol']); ts = pd.Timestamp(item['time']); src = str(item['source'])
            if (sym, ts, src) in early_keys:
                kept.append(item)
                continue
            s0 = score_at_cached(score_cache, raw, sym, ts, cfg)
            s1 = score_at_cached(score_cache, raw, sym, ts - pd.Timedelta(minutes=1), cfg)
            jump = s0 - s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
            if np.isfinite(jump) and jump >= th:
                vetoed.append(dict(symbol=sym, source=src, time=ts,
                                   live_score_t_1=s1, live_score_t=s0,
                                   last_1m_jump=jump, event_score=float(item['event'][2])))
            else:
                kept.append(item)

        tr = integ.simulate(packed, states, kept)
        name = f'R3_B05_PLUS_VETO{int(th)}'
        st = metrics(name, tr)
        st['advanced_tags'] = len(changes)
        st['vetoed_remaining_tags'] = len(vetoed)
        rows.append(st)
        trade_parts.append(tr.assign(case=name))
        if vetoed:
            q = pd.DataFrame(vetoed); q['case'] = name; veto_parts.append(q)
        print(name, st, flush=True)

    summary = pd.DataFrame(rows)
    trades = pd.concat(trade_parts, ignore_index=True, sort=False)
    vetoes = pd.concat(veto_parts, ignore_index=True, sort=False) if veto_parts else pd.DataFrame()

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== R3_B05 ADVANCED TAGS ===')
    print(changes.to_string(index=False) if len(changes) else 'NONE')

    print('\n=== VETOES BY CASE / SOURCE ===')
    if len(vetoes):
        print(vetoes.groupby(['case','source']).size().to_string())
    else:
        print('NONE')

    # Match veto candidates to baseline realized trades for winner/loss inspection.
    if len(vetoes):
        be = baseline[['symbol','entry_time','pnl_pct','reason','source']].copy()
        be['symbol'] = be.symbol.astype(str).str.zfill(6)
        vx = vetoes.merge(be, left_on=['symbol','time'], right_on=['symbol','entry_time'], how='left', suffixes=('','_baseline'))
        print('\n=== VETOES MATCHED TO A55 EXECUTED TRADES ===')
        print(vx.to_string(index=False))
        vx.to_csv(OUT / 'vetoes_matched_a55.csv', index=False)

    # Match advanced entries against baseline and R3 realized paths.
    if len(changes):
        be = baseline[['symbol','entry_time','entry_price','pnl_pct','reason','source']].copy()
        be['symbol'] = be.symbol.astype(str).str.zfill(6)
        rr = r3.copy(); rr['symbol'] = rr.symbol.astype(str).str.zfill(6)
        z = changes.merge(be, left_on=['symbol','original_time'], right_on=['symbol','entry_time'], how='left', suffixes=('','_baseline'))
        z = z.merge(rr[['symbol','entry_time','entry_price','pnl_pct','reason']], left_on=['symbol','early_time'], right_on=['symbol','entry_time'], how='left', suffixes=('_baseline','_early'))
        if 'pnl_pct_baseline' in z.columns and 'pnl_pct_early' in z.columns:
            z['gross_pnl_delta_pct'] = pd.to_numeric(z.pnl_pct_early, errors='coerce') - pd.to_numeric(z.pnl_pct_baseline, errors='coerce')
        print('\n=== ADVANCED SIGNAL OUTCOME MATCH ===')
        print(z.to_string(index=False))
        z.to_csv(OUT / 'advanced_signal_outcomes.csv', index=False)

    print('\n=== NET LOSSES <= -3% ===')
    zz = trades.copy(); zz['net_pct'] = pd.to_numeric(zz.pnl_pct, errors='coerce') - FEE
    zz = zz[zz.net_pct <= -3.0]
    cols = [c for c in ['case','source','symbol','entry_time','exit_time','pnl_pct','net_pct','reason'] if c in zz.columns]
    print(zz[cols].to_string(index=False) if len(zz) else 'NONE')

    summary.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)
    changes.to_csv(OUT / 'advanced_tags.csv', index=False)
    vetoes.to_csv(OUT / 'vetoed_remaining_normal_entries.csv', index=False)
    print('\nWROTE', OUT / 'summary.csv')
    print('WROTE', OUT / 'trades.csv')
    print('WROTE', OUT / 'advanced_tags.csv')
    print('WROTE', OUT / 'vetoed_remaining_normal_entries.csv')


if __name__ == '__main__':
    main()
