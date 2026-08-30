from __future__ import annotations

"""Validate KR-discovered last-1m live-score spike veto on fresh US V21E map.

Baseline is the corrected Slow-turn dual-gate at burst threshold 55.
No production logic is changed. Native USD / original ET only.
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
import tools.validate_engine5_v21e_slow_turn_dual_gate_us as dual
import tools.diagnose_engine5_v22_preentry_minute_scores as live_diag

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP = ROOT / 'v21e_fresh_map.pkl'
OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_late_score_spike_veto_us')
FEE = 0.25
BURST_THRESHOLD = 55.0
JUMP_THRESHOLDS = [15.0, 20.0, 25.0, 30.0]
TARGET_SYMBOL = '00SOXL'
TARGET_TIMES = [
    pd.Timestamp('2026-07-02 09:51:00', tz='America/New_York'),
    pd.Timestamp('2026-07-02 14:10:00', tz='America/New_York'),
]


def n(x): return str(x).zfill(6)
def num(x): return pd.to_numeric(x, errors='coerce')

def minute(ts):
    t = pd.Timestamp(ts)
    return t.hour * 60 + t.minute


def metrics(label, trades):
    gross = num(trades.get('pnl_pct')).dropna() if len(trades) else pd.Series(dtype=float)
    net = gross - FEE
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        case=label, trades=len(net), wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean()*100) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=(gp/gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
        max_win_pct=float(net.max()) if len(net) else np.nan,
        net_loss_ge_3_count=int((net <= -3.0).sum()) if len(net) else 0,
    )


def build_corrected_55_tags(raw, cfg, completed, micros, old_tags):
    print('=== REBUILD FRESH PROVISIONAL FRAMES ===', flush=True)
    pf = {}
    for i, s in enumerate(raw, 1):
        pf[s] = uscache.build_minimal_provisional_fast(raw[s], cfg, completed[s])
        print(f'[{i}/{len(raw)}] {s} rows={len(pf[s])}', flush=True)

    old_loader = revised.st.load_or_build_cache
    revised.st.load_or_build_cache = lambda sym, *_: (pf[n(sym)], micros[n(sym)])
    try:
        allslow = revised.build_all_slow(raw, cfg, completed, micros)
    finally:
        revised.st.load_or_build_cache = old_loader

    allslow = e.normalize_slow_boundary_e(allslow)
    sel = revised.select_revised(allslow, dual.CUT).copy()
    sel = sel[(sel.entry_time.map(minute) >= e.US_BUY_START_MINUTE) &
              (sel.entry_time.map(minute) < e.US_NO_ENTRY_MINUTE)].copy()
    sel['symbol'] = sel.symbol.astype(str).str.zfill(6)
    sel['entry_time'] = pd.to_datetime(sel.entry_time)

    score_rows = []
    for _, r in sel.iterrows():
        d = v2.episode_features_for_candidate(r, pf[n(r.symbol)], micros[n(r.symbol)])
        if d is None or not np.isfinite(float(d.get('transition_score', np.nan))):
            raise SystemExit(f'SCORE FAILURE {r.symbol} {r.entry_time}: {d}')
        tmp = r.to_dict(); tmp.update(d)
        ext = v3.extended_rsi50(pd.Series(tmp), pf[n(r.symbol)])
        d.update(ext)
        d['burst_score'] = float(d['transition_score']) + float(ext['rsi50_bonus'])
        score_rows.append(d)

    scores = pd.DataFrame(score_rows, index=sel.index)
    x = pd.concat([sel.reset_index(drop=True), scores.reset_index(drop=True)], axis=1)
    x['coherence_gate'] = x.apply(dual.coherence_gate, axis=1)

    non_slow = [z for z in old_tags if z['source'] != 'SLOW_TURN_E']
    slow_tags = []
    for _, r in x.iterrows():
        mode = dual.mode_for(r, BURST_THRESHOLD)
        if mode == 'REJECT':
            continue
        transport = float(r.burst_score) if mode in ('BURST','BOTH') and float(r.burst_score) >= 50.0 else 50.0
        slow_tags.append(dict(
            source='SLOW_TURN_E', symbol=n(r.symbol), time=pd.Timestamp(r.entry_time),
            event=dual.replace_score(r.event, transport),
            meta={'regime': str(r.regime), 'decision_mode': mode,
                  'burst_score': float(r.burst_score), 'coherence_gate': bool(r.coherence_gate)},
        ))
    tags = sorted(non_slow + slow_tags, key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
    return tags


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not MAP.exists(): raise FileNotFoundError(MAP)

    e.apply_us_session_clock()
    with MAP.open('rb') as fh: m = pickle.load(fh)
    if m.get('price_unit') != 'USD' or int(m.get('time_shift_minutes',999)) != 0:
        raise SystemExit('BAD FRESH MAP SEMANTICS: expected native USD / original ET')

    raw = {n(k):v for k,v in m['raw'].items()}
    cfg = m['cfg']
    completed = {n(k):v for k,v in m['completed'].items()}
    micros = {n(k):v for k,v in m['micros'].items()}
    old_tags = list(m['tags'])
    packed = v8.base.pack_exit_events(raw, cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg))

    print('=== US LAST-1M LIVE-SCORE SPIKE VETO | corrected dual-gate 55 ===', flush=True)
    print('Native USD / original ET. Cases A, jump >= 15/20/25/30.', flush=True)

    tags = build_corrected_55_tags(raw, cfg, completed, micros, old_tags)
    baseline = integ.simulate(packed, states, tags)
    bm = metrics('A55', baseline)
    expected = (116, 44, -15.0925)
    guard = bm['trades'] == expected[0] and bm['wins'] == expected[1] and abs(bm['net_sum_pct']-expected[2]) <= 0.001
    print('BASELINE A55', bm)
    print('A55 REPRO:', 'PASS' if guard else 'FAIL')
    if not guard:
        raise SystemExit('Corrected-55 US baseline mismatch; veto test invalid.')

    score_cache = {}
    def live_score(sym, ts):
        key=(sym,pd.Timestamp(ts))
        if key not in score_cache:
            r=live_diag.score_at(raw[sym], pd.Timestamp(ts), cfg)
            score_cache[key]=np.nan if r is None else float(r['live_score'])
        return score_cache[key]

    ann=[]
    for item in tags:
        sym=n(item['symbol']); ts=pd.Timestamp(item['time'])
        s0=live_score(sym,ts); s1=live_score(sym,ts-pd.Timedelta(minutes=1))
        jump=s0-s1 if np.isfinite(s0) and np.isfinite(s1) else np.nan
        x=dict(item); x['live_score_t']=s0; x['live_score_t_1']=s1; x['last_1m_jump']=jump
        ann.append(x)

    rows=[bm]; trade_parts=[]; veto_rows=[]
    xb=baseline.copy(); xb['case']='A55'; trade_parts.append(xb)

    for th in JUMP_THRESHOLDS:
        kept=[]; vetoed=[]
        for x in ann:
            if np.isfinite(x['last_1m_jump']) and x['last_1m_jump'] >= th: vetoed.append(x)
            else: kept.append(x)
        tr=integ.simulate(packed,states,kept)
        name=f'VETO_JUMP_GE_{int(th)}'
        st=metrics(name,tr); rows.append(st)
        q=tr.copy(); q['case']=name; trade_parts.append(q)
        for x in vetoed:
            veto_rows.append(dict(case=name,symbol=x['symbol'],source=x['source'],time=x['time'],
                                  live_score_t_1=x['live_score_t_1'],live_score_t=x['live_score_t'],
                                  last_1m_jump=x['last_1m_jump'],event_score=float(x['event'][2])))
        print(name,st,'tagged_vetoes=',len(vetoed),flush=True)

    summary=pd.DataFrame(rows)
    trades=pd.concat(trade_parts,ignore_index=True,sort=False)
    vetoes=pd.DataFrame(veto_rows)

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== VETOES BY SOURCE ===')
    if len(vetoes): print(vetoes.groupby(['case','source']).size().to_string())
    else: print('NONE')

    be=baseline[['symbol','entry_time','pnl_pct','reason','source']].copy()
    be['symbol']=be.symbol.astype(str).str.zfill(6)
    if len(vetoes):
        vx=vetoes.merge(be,left_on=['symbol','time'],right_on=['symbol','entry_time'],how='left',suffixes=('','_baseline'))
        print('\n=== VETOES MATCHED TO A55 EXECUTED TRADES ===')
        print(vx.to_string(index=False))
        vx.to_csv(OUT/'vetoes_matched_baseline.csv',index=False)

    print('\n=== SOXL 2026-07-02 TARGETS ===')
    target=trades[(trades.symbol.astype(str).str.zfill(6)==TARGET_SYMBOL) &
                  (pd.to_datetime(trades.entry_time).isin(TARGET_TIMES))].copy()
    if len(target):
        target['net_pct']=pd.to_numeric(target.pnl_pct,errors='coerce')-FEE
        print(target[['case','source','symbol','entry_time','exit_time','pnl_pct','net_pct','reason']].to_string(index=False))
    else: print('NONE')

    print('\n=== NET LOSSES <= -3% ===')
    z=trades.copy(); z['net_pct']=pd.to_numeric(z.pnl_pct,errors='coerce')-FEE
    z=z[z.net_pct<=-3.0]
    print(z[['case','source','symbol','entry_time','exit_time','pnl_pct','net_pct','reason']].to_string(index=False) if len(z) else 'NONE')

    summary.to_csv(OUT/'summary.csv',index=False)
    trades.to_csv(OUT/'trades.csv',index=False)
    vetoes.to_csv(OUT/'vetoed_candidates.csv',index=False)
    print('\nWROTE',OUT/'summary.csv')
    print('WROTE',OUT/'trades.csv')
    print('WROTE',OUT/'vetoed_candidates.csv')

if __name__=='__main__': main()
