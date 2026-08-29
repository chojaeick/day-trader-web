from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v15_boundary as v15
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
OUT_DIR = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'
# User-reported Kiwoom commission assumption. We print BOTH interpretations:
# A) 0.25% total round-trip cost
# B) 0.25% per side = 0.50% round-trip cost
ROUNDTRIP_COSTS = (0.0025, 0.0050)


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items()
            if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def ekey(ts, e):
    return (str(e[0]).zfill(6), str(pd.Timestamp(ts)))


def event_keys(events):
    return {ekey(ts, e) for ts, rows in events.items() for e in rows}


def add_unique(dst, ts, e):
    k = ekey(ts, e)
    for x in dst.get(ts, []):
        if ekey(ts, x) == k:
            return
    dst.setdefault(ts, []).append(e)


def row_at_or_before(df, ts):
    q = df[pd.to_datetime(df.time) <= pd.Timestamp(ts)]
    return None if not len(q) else q.iloc[-1]


def features_for(sym, ts, rich, micro15, gaps):
    t = pd.Timestamp(ts)
    minute = t.hour * 60 + t.minute
    rm = row_at_or_before(rich[sym], t)
    st = v15.slope_state_at(micro15[sym], t)
    gap = gaps[sym].get(t.date(), np.nan)
    sensitive = bool(np.isfinite(gap) and gap >= v15.GAP_PCT and OPEN_MINUTE <= minute < v15.MICRO_END_MINUTE)
    return {
        'minute': minute,
        'gap': gap,
        'sensitive': sensitive,
        'would_v15_wait': bool(sensitive and st['block']),
        'down_steps': int(st['down_steps']),
        'fade_ratio': float(st['fade_ratio']) if np.isfinite(st['fade_ratio']) else np.nan,
        'step_ratio': float(st['step_ratio']) if np.isfinite(st['step_ratio']) else np.nan,
        'macd_slope': float(rm.macd_slope_1m) if rm is not None and np.isfinite(rm.macd_slope_1m) else np.nan,
        'spread': float(rm.spread_1m) if rm is not None and np.isfinite(rm.spread_1m) else np.nan,
        'rsi_slope': float(rm.rsi_slope_1m) if rm is not None and np.isfinite(rm.rsi_slope_1m) else np.nan,
    }


def blocked(rule, f):
    # Only the ADDITIVE immediate-entry stream is filtered. V17C base events are never removed.
    if rule == 'ADD_ALL_EXTRA':
        return False
    if rule == 'BLOCK_V15_SEVERE_DECAY':
        return bool(f['would_v15_wait'])
    if rule == 'BLOCK_OPENING_DECAY_SPREAD_NEG':
        return bool(f['sensitive'] and f['down_steps'] >= 2 and np.isfinite(f['spread']) and f['spread'] < 0)
    if rule == 'BLOCK_OPENING_DECAY_SPREAD_RSI_NEG':
        return bool(f['sensitive'] and f['down_steps'] >= 2 and np.isfinite(f['spread']) and f['spread'] < 0 and np.isfinite(f['rsi_slope']) and f['rsi_slope'] <= 0)
    if rule == 'BLOCK_GAP8_DECAY':
        return bool(np.isfinite(f['gap']) and f['gap'] >= 8.0 and f['minute'] < 10*60 and f['down_steps'] >= 2)
    if rule == 'BLOCK_GAP4_SEVERE_OR_SPREADNEG':
        severe = bool(f['would_v15_wait'])
        spread_decay = bool(f['sensitive'] and f['down_steps'] >= 2 and np.isfinite(f['spread']) and f['spread'] < 0)
        return severe or spread_decay
    raise ValueError(rule)


def metrics(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    wins = int((p > 0).sum())
    losses = int((p <= 0).sum())
    gross = float(p.sum()) if len(p) else 0.0
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    pf = gp / gl if gl > 0 else np.inf
    row = dict(label=label, trades=len(p), wins=wins, losses=losses,
               win_pct=wins/len(p)*100 if len(p) else 0.0,
               gross_pct=gross, avg_pct=float(p.mean()) if len(p) else 0.0,
               pf=pf, maxloss_pct=float(p.min()) if len(p) else np.nan)
    for rt in ROUNDTRIP_COSTS:
        cost_pct = rt * 100.0
        net = p - cost_pct
        ngp = float(net[net > 0].sum()) if len(net) else 0.0
        ngl = float(-net[net < 0].sum()) if len(net) else 0.0
        tag = 'net_rt025' if np.isclose(rt, 0.0025) else 'net_rt050'
        row[f'{tag}_gross_pct'] = float(net.sum()) if len(net) else 0.0
        row[f'{tag}_avg_pct'] = float(net.mean()) if len(net) else 0.0
        row[f'{tag}_pf'] = ngp/ngl if ngl > 0 else np.inf
        row[f'{tag}_win_pct'] = float((net > 0).mean()*100) if len(net) else 0.0
    return row


def trade_keys(t):
    if not len(t): return set()
    return set(zip(t.symbol.astype(str).str.zfill(6), pd.to_datetime(t.entry_time).astype(str)))


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))

    # Frozen V17C base.
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_base, added_base, skipped_base = v17b.build_v17b(ev16, scored, waits)
    t_base = multi.simulate_multi(packed, ev_base, states, THRESHOLD)

    # Immediate-entry stream with opening WAIT removed. Only events absent from base become additive candidates.
    ev_exp, added_exp, skipped_exp = v17b.build_v17b(ev10, scored, pd.DataFrame())
    base_keys = event_keys(ev_base)
    extra_candidates = []
    for ts in sorted(ev_exp):
        for e in ev_exp[ts]:
            if ekey(ts, e) not in base_keys:
                extra_candidates.append((pd.Timestamp(ts), e))

    rich = {s: v16.build_rich_micro(raw[s], cfg) for s in raw}
    micro15 = {s: v15.build_1m_micro(raw[s], cfg) for s in raw}
    gaps = {s: v15.daily_gap_map(raw[s]) for s in raw}

    rules = [
        'ADD_ALL_EXTRA',
        'BLOCK_V15_SEVERE_DECAY',
        'BLOCK_OPENING_DECAY_SPREAD_NEG',
        'BLOCK_OPENING_DECAY_SPREAD_RSI_NEG',
        'BLOCK_GAP8_DECAY',
        'BLOCK_GAP4_SEVERE_OR_SPREADNEG',
    ]

    print('=== V17C ADDITIVE CHASE-FILTER SWEEP ===')
    print('V17C is FROZEN base. Candidate rules can only filter ADDITIONAL immediate-entry events; base V17C entries are never removed.')
    print('Fee views: NET_RT025 assumes 0.25% TOTAL round-trip; NET_RT050 assumes 0.25% EACH SIDE = 0.50% round-trip.')
    print('BASE_BREAKOUT_ADDED=', added_base)
    print('BASE_BREAKOUT_SKIPPED=', skipped_base)
    print('EXP_BREAKOUT_ADDED=', added_exp)
    print('EXP_BREAKOUT_SKIPPED=', skipped_exp)
    print('RAW_EXTRA_EVENT_CANDIDATES=', len(extra_candidates))

    rows = []
    br = metrics('V17C_BASE', t_base)
    br['added_event_candidates'] = 0
    br['blocked_event_candidates'] = 0
    br['realized_extra_trades_vs_base'] = 0
    rows.append(br)

    kb = trade_keys(t_base)
    candidate_detail = []

    for rule in rules:
        ev = {ts: list(xs) for ts, xs in ev_base.items()}
        blocked_n = 0
        added_n = 0
        for ts, e in extra_candidates:
            sym = str(e[0]).zfill(6)
            f = features_for(sym, ts, rich, micro15, gaps)
            is_block = blocked(rule, f)
            candidate_detail.append({'rule': rule, 'symbol': sym, 'time': ts, 'blocked': is_block, **f})
            if is_block:
                blocked_n += 1
                continue
            add_unique(ev, ts, e)
            added_n += 1

        t = multi.simulate_multi(packed, ev, states, THRESHOLD)
        r = metrics(rule, t)
        kt = trade_keys(t)
        r['added_event_candidates'] = added_n
        r['blocked_event_candidates'] = blocked_n
        r['realized_extra_trades_vs_base'] = len(kt - kb)
        rows.append(r)
        t.to_csv(f'{OUT_DIR}/v17c_additive_{rule.lower()}.csv', index=False)

    out = pd.DataFrame(rows)
    b = out.iloc[0]
    out['trade_delta_vs_base'] = out.trades - int(b.trades)
    out['gross_delta_vs_base_pp'] = out.gross_pct - float(b.gross_pct)
    out['pf_delta_vs_base'] = out.pf - float(b.pf)
    out['net_rt025_delta_vs_base_pp'] = out.net_rt025_gross_pct - float(b.net_rt025_gross_pct)
    out['net_rt050_delta_vs_base_pp'] = out.net_rt050_gross_pct - float(b.net_rt050_gross_pct)

    print('\n=== RESULTS ===')
    cols = ['label','trades','trade_delta_vs_base','wins','losses','win_pct','gross_pct','pf',
            'added_event_candidates','blocked_event_candidates','realized_extra_trades_vs_base',
            'net_rt025_gross_pct','net_rt025_pf','net_rt025_win_pct',
            'net_rt050_gross_pct','net_rt050_pf','net_rt050_win_pct',
            'gross_delta_vs_base_pp','net_rt025_delta_vs_base_pp','net_rt050_delta_vs_base_pp']
    print(out[cols].sort_values(['net_rt025_gross_pct','gross_pct'], ascending=False).to_string(index=False))

    print('\n=== RANK BY GROSS (NO FEE) ===')
    print(out[['label','trades','gross_pct','pf','gross_delta_vs_base_pp']].sort_values('gross_pct', ascending=False).to_string(index=False))
    print('\n=== RANK BY NET, 0.25% TOTAL ROUND-TRIP ===')
    print(out[['label','trades','net_rt025_gross_pct','net_rt025_pf','net_rt025_win_pct','net_rt025_delta_vs_base_pp']].sort_values('net_rt025_gross_pct', ascending=False).to_string(index=False))
    print('\n=== RANK BY NET, 0.25% EACH SIDE (0.50% ROUND-TRIP) ===')
    print(out[['label','trades','net_rt050_gross_pct','net_rt050_pf','net_rt050_win_pct','net_rt050_delta_vs_base_pp']].sort_values('net_rt050_gross_pct', ascending=False).to_string(index=False))

    summary_path = f'{OUT_DIR}/v17c_additive_chase_filter_sweep_summary.csv'
    detail_path = f'{OUT_DIR}/v17c_additive_chase_filter_candidate_detail.csv'
    out.to_csv(summary_path, index=False)
    pd.DataFrame(candidate_detail).to_csv(detail_path, index=False)
    print('\n[SUMMARY CSV]', summary_path)
    print('[CANDIDATE DETAIL CSV]', detail_path)


if __name__ == '__main__':
    main()
