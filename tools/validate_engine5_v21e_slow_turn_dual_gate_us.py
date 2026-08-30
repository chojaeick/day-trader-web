from __future__ import annotations

"""Validate the corrected EXISTING Slow-turn decision logic on fresh V21E.

Uses the already-created fresh V21E map (native USD, original ET) as the source of raw,
scored, completed and micro frames. It does NOT load legacy us_e_core caches and does not
remap SQLite again. Provisional frames are rebuilt in memory from the fresh raw map.

Only SLOW_TURN_E eligibility is changed:
  BURST: transition score (including fast RSI-50 cross bonus) >= diagnostic threshold
  OR
  COHERENCE: joint5>=0.80, joint1>=0.70, price_progress>=1.00%

V20E, V_REBOUND_E, exits, conflict ordering and position ownership are unchanged.
Thresholds 50/55/60 are diagnostics only.
"""

import pickle
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised
import tools.validate_engine5_integrated_slow_turn_transition_score_v2 as v2
import tools.validate_engine5_slow_turn_rsi50_bonus_v3 as v3
import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_us_e_all_versions as e

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
OUT_SUM = ROOT / 'v21e_slow_turn_dual_gate_us_summary.csv'
OUT_SIG = ROOT / 'v21e_slow_turn_dual_gate_us_signals.csv'
OUT_TR = ROOT / 'v21e_slow_turn_dual_gate_us_trades.csv'
CUT = -0.15
FEE_RT_PCT = 0.25
THRESHOLDS = (50.0, 55.0, 60.0)
TARGET_SYMBOL = '00SOXL'
TARGET_TIME = pd.Timestamp('2026-07-02 09:51:00', tz='America/New_York')


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x, errors='coerce')

def minute(ts):
    t = pd.Timestamp(ts)
    return t.hour * 60 + t.minute


def replace_score(event, score):
    z = list(event)
    z[2] = float(score)
    return tuple(z)


def coherence_gate(r):
    j5 = float(r.get('joint5_persistence', np.nan)) if pd.notna(r.get('joint5_persistence', np.nan)) else np.nan
    j1 = float(r.get('joint1_persistence', np.nan)) if pd.notna(r.get('joint1_persistence', np.nan)) else np.nan
    px = float(r.get('price_progress_1m_pct', np.nan)) if pd.notna(r.get('price_progress_1m_pct', np.nan)) else np.nan
    return bool(np.isfinite(j5) and j5 >= 0.80 and np.isfinite(j1) and j1 >= 0.70 and np.isfinite(px) and px >= 1.00)


def mode_for(r, th):
    b = float(r.burst_score) >= float(th)
    c = bool(r.coherence_gate)
    if b and c: return 'BOTH'
    if b: return 'BURST'
    if c: return 'COHERENCE'
    return 'REJECT'


def metrics(trades):
    gross = num(trades.get('pnl_pct')).dropna() if len(trades) else pd.Series(dtype=float)
    net = gross - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        trades=len(net), wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
    )


def main():
    if not MAP.exists():
        raise FileNotFoundError(MAP)

    e.apply_us_session_clock()
    with MAP.open('rb') as fh:
        m = pickle.load(fh)

    if m.get('price_unit') != 'USD' or int(m.get('time_shift_minutes', 999)) != 0:
        raise SystemExit('BAD FRESH MAP SEMANTICS: expected native USD / original ET')

    raw = {n(k): v for k, v in m['raw'].items()}
    cfg = m['cfg']
    scored = {n(k): v for k, v in m['scored'].items()}
    completed = {n(k): v for k, v in m['completed'].items()}
    micros = {n(k): v for k, v in m['micros'].items()}
    old_tags = list(m['tags'])

    packed = v8.base.pack_exit_events(raw, cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg))

    print('=== V21E SLOW-TURN DUAL GATE | USD native / original ET ===', flush=True)
    print('Fresh map reused; provisional frames rebuilt in memory. No SQLite remap.', flush=True)

    # First, the old fresh V21E map must reproduce exactly before any revised comparison.
    old_tr = integ.simulate(packed, states, old_tags)
    old_m = metrics(old_tr)
    expected_trades = 141
    expected_net = -25.7378
    repro = old_m['trades'] == expected_trades and abs(old_m['net_sum_pct'] - expected_net) <= 0.001
    print(f"OLD V21E: trades={old_m['trades']} wins={old_m['wins']} WR={old_m['win_pct']:.2f}% net={old_m['net_sum_pct']:+.4f}% PF={old_m['pf']:.3f}")
    if not repro:
        raise SystemExit('REPRO CHECK FAIL: fresh V21E baseline mismatch; stop before interpreting dual gate')
    print('REPRO CHECK: fresh V21E baseline PASS', flush=True)

    print('=== REBUILD FRESH PROVISIONAL FRAMES ===', flush=True)
    pf = {}
    for i, s in enumerate(raw, 1):
        pf[s] = uscache.build_minimal_provisional_fast(raw[s], cfg, completed[s])
        print(f'[{i}/{len(raw)}] {s} rows={len(pf[s])}', flush=True)

    # Recreate exactly the same existing Slow-turn-E selected candidates used by fresh V21E.
    old_loader = revised.st.load_or_build_cache
    revised.st.load_or_build_cache = lambda sym, *_: (pf[n(sym)], micros[n(sym)])
    try:
        allslow = revised.build_all_slow(raw, cfg, completed, micros)
    finally:
        revised.st.load_or_build_cache = old_loader
    allslow = e.normalize_slow_boundary_e(allslow)
    sel = revised.select_revised(allslow, CUT).copy()
    sel = sel[(sel.entry_time.map(minute) >= e.US_BUY_START_MINUTE) & (sel.entry_time.map(minute) < e.US_NO_ENTRY_MINUTE)].copy()
    sel['symbol'] = sel.symbol.astype(str).str.zfill(6)
    sel['entry_time'] = pd.to_datetime(sel.entry_time)

    # Score the selected candidates with the same KR-corrected BURST definition.
    score_rows = []
    for _, r in sel.iterrows():
        d = v2.episode_features_for_candidate(r, pf[n(r.symbol)], micros[n(r.symbol)])
        if d is None or not np.isfinite(float(d.get('transition_score', np.nan))):
            raise SystemExit(f"SCORE FAILURE {r.symbol} {r.entry_time}: {d}")
        tmp = r.to_dict()
        tmp.update(d)
        ext = v3.extended_rsi50(pd.Series(tmp), pf[n(r.symbol)])
        d.update(ext)
        d['burst_score'] = float(d['transition_score']) + float(ext['rsi50_bonus'])
        score_rows.append(d)
    scores = pd.DataFrame(score_rows, index=sel.index)
    x = pd.concat([sel.reset_index(drop=True), scores.reset_index(drop=True)], axis=1)
    x['coherence_gate'] = x.apply(coherence_gate, axis=1)

    non_slow = [z for z in old_tags if z['source'] != 'SLOW_TURN_E']
    rows = [dict(burst_threshold='OLD', slow_selected=sum(z['source'] == 'SLOW_TURN_E' for z in old_tags),
                 burst_only=np.nan, coherence_only=np.nan, both=np.nan, **old_m)]
    trade_parts = []
    signal_parts = []

    for th in THRESHOLDS:
        tags = []
        diag = []
        for _, r in x.iterrows():
            mode = mode_for(r, th)
            transport = np.nan
            if mode != 'REJECT':
                transport = float(r.burst_score) if mode in ('BURST', 'BOTH') and float(r.burst_score) >= 50.0 else 50.0
                tags.append(dict(
                    source='SLOW_TURN_E', symbol=n(r.symbol), time=pd.Timestamp(r.entry_time),
                    event=replace_score(r.event, transport),
                    meta={'regime': str(r.regime), 'decision_mode': mode,
                          'burst_score': float(r.burst_score), 'coherence_gate': bool(r.coherence_gate)},
                ))
            diag.append(dict(
                symbol=n(r.symbol), entry_time=pd.Timestamp(r.entry_time), regime=str(r.regime),
                burst_score=float(r.burst_score), transition_score=float(r.transition_score),
                rsi50_bonus=float(r.rsi50_bonus), coherence_gate=bool(r.coherence_gate),
                joint5_persistence=float(r.joint5_persistence) if pd.notna(r.joint5_persistence) else np.nan,
                joint1_persistence=float(r.joint1_persistence) if pd.notna(r.joint1_persistence) else np.nan,
                price_progress_1m_pct=float(r.price_progress_1m_pct) if pd.notna(r.price_progress_1m_pct) else np.nan,
                decision_mode=mode, transport_score=transport,
            ))

        all_tags = sorted(non_slow + tags, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
        tr = integ.simulate(packed, states, all_tags)
        st = metrics(tr)
        modes = pd.Series([z['meta']['decision_mode'] for z in tags], dtype=str)
        rows.append(dict(
            burst_threshold=th, slow_selected=len(tags),
            burst_only=int((modes == 'BURST').sum()), coherence_only=int((modes == 'COHERENCE').sum()),
            both=int((modes == 'BOTH').sum()), **st,
        ))
        q = tr.copy(); q['burst_threshold'] = th; trade_parts.append(q)
        q2 = pd.DataFrame(diag); q2['burst_threshold'] = th; signal_parts.append(q2)

    summary = pd.DataFrame(rows)
    summary.to_csv(OUT_SUM, index=False)
    if trade_parts: pd.concat(trade_parts, ignore_index=True).to_csv(OUT_TR, index=False)
    detail = pd.concat(signal_parts, ignore_index=True) if signal_parts else pd.DataFrame()
    if len(detail): detail.to_csv(OUT_SIG, index=False)

    print('\n=== US FULL INTEGRATION ===')
    show = ['burst_threshold','slow_selected','burst_only','coherence_only','both','trades','wins','win_pct','net_sum_pct','pf','max_loss_pct']
    print(summary[show].to_string(index=False, float_format=lambda v: f'{v:.4f}'))

    print('\n=== TARGET SOXL 2026-07-02 09:51 ET ===')
    if len(detail):
        q = detail[(detail.symbol == TARGET_SYMBOL) & (pd.to_datetime(detail.entry_time) == TARGET_TIME)]
    else:
        q = detail
    if q.empty:
        print('NOT FOUND')
    else:
        cols = ['burst_threshold','burst_score','transition_score','rsi50_bonus','coherence_gate',
                'joint5_persistence','joint1_persistence','price_progress_1m_pct','decision_mode']
        print(q[cols].to_string(index=False, float_format=lambda v: f'{v:.4f}'))

    print('\nREADING:')
    print('- OLD must stay 141 trades / net -25.7378% before revised rows are interpreted.')
    print('- The target SOXL 09:51 false entry should be REJECT if the corrected semantics transfer.')
    print('- 50/55/60 remain diagnostics; do not freeze a threshold from KR or US alone.')
    print('WROTE', OUT_SUM)
    print('WROTE', OUT_TR)
    print('WROTE', OUT_SIG)


if __name__ == '__main__':
    main()
