from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pickle

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.diagnose_v20_transition_structure_targets as st
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
CACHE_DIR = OUT_DIR / 'v20_transition_cache'
FEE_RT_PCT = 0.25
THRESHOLD = 50
REL_MIN = 1.45
RAW_MINS = [20.0, 30.0, 40.0]
SLOPE_LOOKBACKS = [3, 5]
STOP_CAPS = [0.5, 0.7, 1.0, 1.2]
MIN_POS_RATIO = 0.50


def norm_sym(x):
    return str(x).zfill(6)


def finite(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else np.nan
    except Exception:
        return np.nan


def stats(label, trades):
    g = pd.to_numeric(trades.pnl_pct, errors='coerce').dropna() if len(trades) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(
        label=label, trades=len(n), wins=int((n > 0).sum()), losses=int((n <= 0).sum()),
        win_pct=float((n > 0).mean() * 100.0) if len(n) else 0.0,
        net_sum_pct=float(n.sum()) if len(n) else 0.0,
        net_avg_pct=float(n.mean()) if len(n) else 0.0,
        pf=(gp / gl) if gl > 0 else np.inf,
        max_loss_pct=float(n.min()) if len(n) else np.nan,
    )


def load_or_build_cache(sym, raw_bars, cfg, completed):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f'{sym}_provisional_micro.pkl'
    if path.exists():
        with path.open('rb') as f:
            obj = pickle.load(f)
        print(f'CACHE HIT {sym}', flush=True)
        return obj['provisional'], obj['micro']
    print(f'CACHE BUILD {sym}', flush=True)
    pf = rt.build_provisional_5m(raw_bars, cfg)
    pf = rt.add_provisional_strength(pf, completed)
    m = h.build_micro(raw_bars, cfg)
    with path.open('wb') as f:
        pickle.dump({'provisional': pf, 'micro': m}, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f'CACHE WROTE {sym}', flush=True)
    return pf, m


def make_event(sym, completed_row, price):
    iu = finite(completed_row.get('inner_upper', np.nan))
    il = finite(completed_row.get('inner_lower', np.nan))
    ou = finite(completed_row.get('outer_upper', np.nan))
    mid = finite(completed_row.get('mid', np.nan))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(band_r) or band_r <= 0:
        return None
    score = max(THRESHOLD, finite(completed_row.get('entry_score', THRESHOLD)))
    extended = bool(np.isfinite(ou) and float(price) > ou)
    return (
        norm_sym(sym), float(price), float(score),
        finite(completed_row.get('macd_slope_spread_strength', np.nan)),
        finite(completed_row.get('rsi_slope_strength', np.nan)),
        float(band_r), float(band_r), iu, il, ou, mid, extended, False,
    )


def build_candidates(sym, pf, micro, scored_frame):
    z = st.add_structure_features(pf, micro).sort_values('time').reset_index(drop=True)
    mid = pd.to_numeric(z.mid_slope8, errors='coerce')
    d = mid.diff()
    z['mid_non_up'] = mid <= 0
    z['momentum_up'] = ((pd.to_numeric(z.macd_slope, errors='coerce') > 0) &
                        (pd.to_numeric(z.rsi_slope, errors='coerce') > 0))
    z['rel_ok'] = pd.to_numeric(z.strength_rel, errors='coerce') >= REL_MIN
    for lb in SLOPE_LOOKBACKS:
        z[f'slope_gain_{lb}'] = mid - mid.shift(lb)
        z[f'slope_pos_ratio_{lb}'] = (d > 0).rolling(lb, min_periods=lb).mean()

    rows = []
    for raw_min in RAW_MINS:
        for lb in SLOPE_LOOKBACKS:
            recovery = ((z[f'slope_gain_{lb}'] > 0) &
                        (z[f'slope_pos_ratio_{lb}'] >= MIN_POS_RATIO))
            ready = (z.mid_non_up & recovery & z.momentum_up & z.rel_ok &
                     (pd.to_numeric(z.gap_delta, errors='coerce') >= raw_min))
            for mode, structure_col, stop_col in [
                ('BOX_BREAK', 'box_break', 'break_stop'),
                ('V_PULLBACK_RECLAIM', 'v_reclaim', 'v_stop'),
            ]:
                hit = z[ready & z[structure_col].fillna(False)].copy()
                if hit.empty:
                    continue
                for _, r in hit.iterrows():
                    ts = pd.Timestamp(r.time)
                    minute = ts.hour * 60 + ts.minute
                    if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
                        continue
                    price = finite(r.close)
                    stop = finite(r[stop_col])
                    if not (np.isfinite(price) and price > 0 and np.isfinite(stop) and stop < price):
                        continue
                    stop_dist = (price / stop - 1.0) * 100.0
                    q5 = scored_frame[scored_frame.time <= ts.floor('5min')]
                    if q5.empty:
                        continue
                    ev = make_event(sym, q5.iloc[-1], price)
                    if ev is None:
                        continue
                    rows.append(dict(
                        symbol=norm_sym(sym), time=ts, mode=mode, raw_min=raw_min, slope_lb=lb,
                        price=price, structural_stop=stop, stop_dist_pct=stop_dist,
                        raw=finite(r.gap_delta), rel=finite(r.strength_rel), rsi=finite(r.rsi),
                        mid_slope8=finite(r.mid_slope8), slope_gain=finite(r[f'slope_gain_{lb}']),
                        slope_pos_ratio=finite(r[f'slope_pos_ratio_{lb}']), event=ev,
                    ))
    return pd.DataFrame(rows)


def events_from_candidates(cand, raw_min, lb, stop_cap, mode=None):
    q = cand[(cand.raw_min == raw_min) & (cand.slope_lb == lb) &
             (cand.stop_dist_pct <= stop_cap)].copy()
    if mode is not None:
        q = q[q['mode'] == mode]
    if q.empty:
        return {}, q
    # Prevent minute-by-minute duplicate chasing: first qualifying transition per symbol/day/mode.
    q['day'] = pd.to_datetime(q.time).dt.date
    q = q.sort_values('time').drop_duplicates(['symbol', 'day', 'mode'], keep='first')
    events = {}
    for _, r in q.iterrows():
        events.setdefault(pd.Timestamp(r.time), []).append(r.event)
    return events, q


def merge_events(a, b):
    out = {pd.Timestamp(k): list(v) for k, v in a.items()}
    for k, items in b.items():
        out.setdefault(pd.Timestamp(k), []).extend(items)
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = {norm_sym(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    print('=== V20 TRANSITION STRUCTURE FULL VALIDATION ===', flush=True)
    print('V20 established-uptrend path is unchanged.', flush=True)
    print('Transition path: non-up slope recovery + MACD/RSI + BOX_BREAK or V_PULLBACK_RECLAIM.', flush=True)
    print('Stop caps currently FILTER entries by structural-stop distance; exit logic itself is unchanged in this pass.', flush=True)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {norm_sym(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored0 = reweight(f10, cfg, 0.0)
    scored = {norm_sym(s): f for s, f in scored0.items()}
    strength_frames = {sym: ms.add_strength(f) for sym, f in scored.items()}
    completed = {sym: rt.add_completed_strength(f) for sym, f in scored.items()}

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)

    micros = {}
    candidates = []
    total_syms = len(raw)
    for i, (sym, bars) in enumerate(raw.items(), 1):
        print(f'[{i}/{total_syms}] FEATURES {sym}', flush=True)
        if sym not in completed or sym not in scored:
            continue
        pf, m = load_or_build_cache(sym, bars, cfg, completed[sym])
        micros[sym] = m
        c = build_candidates(sym, pf, m, scored[sym])
        if len(c):
            candidates.append(c)

    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength_frames, raw_min=52.0, rel_min=1.45)
    base_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)
    base_stat = stats('V20_BASE', base_trades)
    print('\nBASE', pd.DataFrame([base_stat]).to_string(index=False), flush=True)

    cand = pd.concat(candidates, ignore_index=True) if candidates else pd.DataFrame()
    if cand.empty:
        print('NO TRANSITION CANDIDATES', flush=True)
        return

    cand_out = cand.drop(columns=['event']).copy()
    cand_out.to_csv(OUT_DIR / 'v20_transition_structure_candidates.csv', index=False)

    rows = []
    for raw_min in RAW_MINS:
        for lb in SLOPE_LOOKBACKS:
            for cap in STOP_CAPS:
                all_ev, all_q = events_from_candidates(cand, raw_min, lb, cap)
                merged = merge_events(ev20, all_ev)
                extra_t = multi.simulate_multi(packed, all_ev, states, THRESHOLD)
                merged_t = multi.simulate_multi(packed, merged, states, THRESHOLD)
                sm = stats(f'RAW{raw_min:g}_LB{lb}_STOP{cap:.1f}', merged_t)
                se = stats('EXTRA', extra_t)
                box_ev, box_q = events_from_candidates(cand, raw_min, lb, cap, 'BOX_BREAK')
                v_ev, v_q = events_from_candidates(cand, raw_min, lb, cap, 'V_PULLBACK_RECLAIM')
                box_t = multi.simulate_multi(packed, box_ev, states, THRESHOLD)
                v_t = multi.simulate_multi(packed, v_ev, states, THRESHOLD)
                sb = stats('BOX', box_t)
                sv = stats('V', v_t)
                sm.update(
                    raw_min=raw_min, slope_lb=lb, stop_cap_pct=cap,
                    extra_signals=len(all_q), extra_trades=se['trades'], extra_win_pct=se['win_pct'],
                    extra_net_sum_pct=se['net_sum_pct'], extra_pf=se['pf'],
                    box_signals=len(box_q), box_trades=sb['trades'], box_win_pct=sb['win_pct'],
                    box_net_sum_pct=sb['net_sum_pct'], box_pf=sb['pf'],
                    v_signals=len(v_q), v_trades=sv['trades'], v_win_pct=sv['win_pct'],
                    v_net_sum_pct=sv['net_sum_pct'], v_pf=sv['pf'],
                )
                rows.append(sm)
                print(
                    f"RAW{raw_min:g} LB{lb} STOP<={cap:.1f}% | merged {sm['trades']} WR {sm['win_pct']:.2f}% NET {sm['net_sum_pct']:+.3f}% "
                    f"| extra {se['trades']} WR {se['win_pct']:.2f}% NET {se['net_sum_pct']:+.3f}% "
                    f"| BOX {sb['trades']} NET {sb['net_sum_pct']:+.3f}% | V {sv['trades']} NET {sv['net_sum_pct']:+.3f}%",
                    flush=True,
                )

    summary = pd.DataFrame(rows).sort_values(['net_sum_pct', 'win_pct', 'pf'], ascending=False)
    summary.to_csv(OUT_DIR / 'v20_transition_structure_summary.csv', index=False)

    print('\n=== TOP 20 ===', flush=True)
    cols = ['label','trades','wins','losses','win_pct','net_sum_pct','net_avg_pct','pf','max_loss_pct',
            'extra_trades','extra_win_pct','extra_net_sum_pct','extra_pf',
            'box_trades','box_win_pct','box_net_sum_pct','box_pf',
            'v_trades','v_win_pct','v_net_sum_pct','v_pf']
    print(summary[[c for c in cols if c in summary.columns]].head(20).to_string(index=False), flush=True)
    print('\nWROTE', OUT_DIR / 'v20_transition_structure_summary.csv', flush=True)
    print('WROTE', OUT_DIR / 'v20_transition_structure_candidates.csv', flush=True)


if __name__ == '__main__':
    main()
