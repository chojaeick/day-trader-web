from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
THRESHOLD = 50
FEE_RT_PCT = 0.25

# This is intentionally a diagnostic sweep, not a frozen rule.
SLOPE_LBS = [3, 5]
SLOPE_POS_RATIOS = [0.67, 0.80]
MICRO_POS_RATIOS = [0.67, 0.80]
MICRO_LB = 3
READY_MAX_MIN = 5


def n(x):
    return str(x).zfill(6)


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def add_slow_turn_features(pf: pd.DataFrame, micro: pd.DataFrame) -> pd.DataFrame:
    z = pf.copy().sort_values('time').reset_index(drop=True)
    z['time'] = pd.to_datetime(z['time'])
    mid = pd.to_numeric(z['mid_slope8'], errors='coerce')
    dm = mid.diff()
    for lb in SLOPE_LBS:
        z[f'slope_gain_{lb}'] = mid - mid.shift(lb)
        z[f'slope_pos_ratio_{lb}'] = (dm > 0).rolling(lb, min_periods=lb).mean()

    m = micro.copy().sort_values('time').reset_index(drop=True)
    m['time'] = pd.to_datetime(m['time'])
    close = pd.to_numeric(m['close'], errors='coerce')
    low = pd.to_numeric(m['low'], errors='coerce')
    high = pd.to_numeric(m['high'], errors='coerce')
    gd = pd.to_numeric(m['macd_gap_delta_1m'], errors='coerce')
    rs = pd.to_numeric(m['rsi_slope_1m'], errors='coerce')

    # Prior-bar structure only. Current close must prove that price has actually turned.
    m['prior_low_3'] = low.shift(1).rolling(3, min_periods=2).min()
    m['prior_high_3'] = high.shift(1).rolling(3, min_periods=2).max()
    m['higher_low'] = m['prior_low_3'] > low.shift(4).rolling(3, min_periods=2).min()
    m['higher_high_break'] = close > m['prior_high_3']
    m['gap_pos_ratio_3'] = (gd > 0).rolling(MICRO_LB, min_periods=MICRO_LB).mean()
    m['rsi_pos_ratio_3'] = (rs > 0).rolling(MICRO_LB, min_periods=MICRO_LB).mean()

    keep = ['time','close','low','high','macd_gap_delta_1m','rsi_slope_1m',
            'prior_low_3','prior_high_3','higher_low','higher_high_break',
            'gap_pos_ratio_3','rsi_pos_ratio_3']
    return z, m[keep]


def first_micro_confirmation(m: pd.DataFrame, ready_ts: pd.Timestamp, micro_ratio: float):
    q = m[(m.time >= ready_ts) & (m.time < ready_ts + pd.Timedelta(minutes=READY_MAX_MIN))].copy()
    if q.empty:
        return None
    ok = (
        q['higher_low'].fillna(False)
        & q['higher_high_break'].fillna(False)
        & (pd.to_numeric(q['gap_pos_ratio_3'], errors='coerce') >= micro_ratio)
        & (pd.to_numeric(q['rsi_pos_ratio_3'], errors='coerce') >= micro_ratio)
        & (pd.to_numeric(q['macd_gap_delta_1m'], errors='coerce') > 0)
        & (pd.to_numeric(q['rsi_slope_1m'], errors='coerce') > 0)
    )
    hit = q[ok]
    return None if hit.empty else hit.iloc[0]


def event_from_row(sym: str, row5, mr):
    iu = finite(row5.get('inner_upper')); il = finite(row5.get('inner_lower'))
    ou = finite(row5.get('outer_upper')); mid = finite(row5.get('mid'))
    px = finite(mr.get('close'))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(px) or not np.isfinite(band_r) or band_r <= 0:
        return None
    # Prototype path is isolated from the established V20 score gate. Give it the
    # simulator threshold only after its own transition + 1m confirmation passed.
    score = max(float(THRESHOLD), finite(row5.get('entry_score', THRESHOLD)))
    stop_ref = finite(mr.get('prior_low_3'))
    stop_dist = px - stop_ref if np.isfinite(stop_ref) and stop_ref < px else band_r
    return (
        n(sym), px, score,
        finite(row5.get('macd_slope_spread_strength', np.nan)),
        finite(row5.get('rsi_slope_strength', np.nan)),
        float(band_r), float(stop_dist), iu, il, ou, mid,
        bool(np.isfinite(ou) and px > ou), False,
    )


def build_candidates(sym, pf, micro, slope_lb, slope_ratio, micro_ratio):
    z, m = add_slow_turn_features(pf, micro)
    mid = pd.to_numeric(z['mid_slope8'], errors='coerce')
    gd = pd.to_numeric(z.get('gap_delta'), errors='coerce')
    rs = pd.to_numeric(z.get('rsi_slope'), errors='coerce')

    ready = (
        (mid < 0)
        & (pd.to_numeric(z[f'slope_gain_{slope_lb}'], errors='coerce') > 0)
        & (pd.to_numeric(z[f'slope_pos_ratio_{slope_lb}'], errors='coerce') >= slope_ratio)
        & (gd > 0)
        & (rs > 0)
    )

    rows = []
    seen_day = set()
    for _, r in z[ready].iterrows():
        ts = pd.Timestamp(r.time)
        minute = ts.hour * 60 + ts.minute
        if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
            continue
        key = (n(sym), ts.date())
        if key in seen_day:
            continue
        mr = first_micro_confirmation(m, ts, micro_ratio)
        if mr is None:
            continue
        ev = event_from_row(sym, r, mr)
        if ev is None:
            continue
        seen_day.add(key)
        rows.append(dict(
            symbol=n(sym), ready_time=ts, entry_time=pd.Timestamp(mr.time),
            entry_price=finite(mr.close), mid_slope8=finite(r.mid_slope8),
            slope_gain=finite(r[f'slope_gain_{slope_lb}']),
            slope_pos_ratio=finite(r[f'slope_pos_ratio_{slope_lb}']),
            gap_delta_5m=finite(r.get('gap_delta')), rsi_slope_5m=finite(r.get('rsi_slope')),
            gap_pos_ratio_1m=finite(mr.gap_pos_ratio_3), rsi_pos_ratio_1m=finite(mr.rsi_pos_ratio_3),
            prior_low_3=finite(mr.prior_low_3), prior_high_3=finite(mr.prior_high_3),
            event=ev, source='SLOW_TURN',
        ))
    return pd.DataFrame(rows)


def stat(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    net = p - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(label=label, trades=len(net), wins=int((net > 0).sum()),
                win_pct=float((net > 0).mean() * 100) if len(net) else 0.0,
                net_sum_pct=float(net.sum()) if len(net) else 0.0,
                pf=(gp / gl if gl > 0 else np.inf),
                max_loss_pct=float(net.min()) if len(net) else np.nan)


def event_stream(df):
    out = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        out.setdefault(pd.Timestamp(r.entry_time), []).append(r.event)
    return out


def merge_streams(a, b):
    out = {pd.Timestamp(k): list(v) for k, v in a.items()}
    for ts, rows in b.items():
        out.setdefault(pd.Timestamp(ts), []).extend(rows)
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
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

    ev10 = sweep.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength, raw_min=52.0, rel_min=1.45)
    base_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)

    print('=== SLOW TURN PROTOTYPE / V20 IS FROZEN ===')
    print(pd.DataFrame([stat('V20_FROZEN', base_trades)]).to_string(index=False))
    print('Rule family under test: 5m mid_slope8 still negative but recovering consistently; 5m MACD/RSI improving; 1m Higher-Low + prior-high break + momentum continuity.')
    print('No V20 rule is changed. Slow-turn events are built separately and only merged for comparison.\n')

    pf_by_symbol = {}
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] build provisional {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        pf_by_symbol[s] = pf

    rows = []
    all_selected = []
    for slb in SLOPE_LBS:
        for sratio in SLOPE_POS_RATIOS:
            for mratio in MICRO_POS_RATIOS:
                parts = []
                for s in raw:
                    q = build_candidates(s, pf_by_symbol[s], micros[s], slb, sratio, mratio)
                    if len(q):
                        parts.append(q)
                cand = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
                sev = event_stream(cand)
                extra = multi.simulate_multi(packed, sev, states, THRESHOLD)
                merged = multi.simulate_multi(packed, merge_streams(ev20, sev), states, THRESHOLD)
                se = stat('EXTRA', extra); smg = stat('MERGED', merged)

                # Regression guard: the established V20 stream itself must remain byte-for-byte
                # conceptually untouched; report overlapping timestamps so later production merge
                # can source-tag/deduplicate instead of silently replacing V20 entries.
                overlap = 0
                v20_keys = {(pd.Timestamp(ts), n(c[0])) for ts, cs in ev20.items() for c in cs}
                if len(cand):
                    overlap = sum((pd.Timestamp(r.entry_time), n(r.symbol)) in v20_keys for _, r in cand.iterrows())
                    x = cand.copy(); x['slope_lb'] = slb; x['slope_ratio_cfg'] = sratio; x['micro_ratio_cfg'] = mratio
                    all_selected.append(x)

                rows.append(dict(
                    slope_lb=slb, slope_pos_ratio=sratio, micro_pos_ratio=mratio,
                    signals=len(cand), overlaps_v20=overlap,
                    extra_trades=se['trades'], extra_wins=se['wins'], extra_win_pct=se['win_pct'],
                    extra_net=se['net_sum_pct'], extra_pf=se['pf'], extra_max_loss=se['max_loss_pct'],
                    merged_trades=smg['trades'], merged_wins=smg['wins'], merged_win_pct=smg['win_pct'],
                    merged_net=smg['net_sum_pct'], merged_pf=smg['pf'], merged_max_loss=smg['max_loss_pct'],
                ))

    summary = pd.DataFrame(rows).sort_values(['extra_net','extra_pf'], ascending=False)
    print('\n=== SWEEP SUMMARY ===')
    print(summary.to_string(index=False))

    summary_path = OUT_DIR / 'slow_turn_prototype_summary.csv'
    summary.to_csv(summary_path, index=False)
    if all_selected:
        sel = pd.concat(all_selected, ignore_index=True)
        sel.drop(columns=['event'], errors='ignore').to_csv(OUT_DIR / 'slow_turn_prototype_candidates.csv', index=False)
        print('\n=== CANDIDATES ===')
        show = ['slope_lb','slope_ratio_cfg','micro_ratio_cfg','symbol','ready_time','entry_time','entry_price',
                'mid_slope8','slope_gain','slope_pos_ratio','gap_delta_5m','rsi_slope_5m',
                'gap_pos_ratio_1m','rsi_pos_ratio_1m','prior_low_3','prior_high_3']
        print(sel[show].sort_values(['entry_time','symbol']).to_string(index=False))

    print('\nREGRESSION BASELINE MUST REMAIN: V20 39 trades / 17 wins / +20.012131% net / PF 1.86292.')
    print('If the first V20_FROZEN line differs materially, stop and report before interpreting slow-turn results.')
    print('WROTE', summary_path)
    print('WROTE', OUT_DIR / 'slow_turn_prototype_candidates.csv')


if __name__ == '__main__':
    main()
