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
import tools.validate_engine5_slow_turn_prototype as slow
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
SLOPE_LB = 3
SLOPE_RATIO = 0.67
MICRO_RATIO = 0.67
MAX_PRINT = 25


def n(x):
    return str(x).zfill(6)


def num(x):
    return pd.to_numeric(x, errors='coerce')


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def stat_from_pnl(pnl: pd.Series):
    p = pd.to_numeric(pnl, errors='coerce').dropna()
    net = p - FEE_RT_PCT
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100.0) if len(net) else 0.0,
        net_sum=float(net.sum()) if len(net) else 0.0,
        avg_net=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss=float(net.min()) if len(net) else np.nan,
    )


def event_from_completed(sym: str, row5, mr):
    iu = finite(row5.get('inner_upper')); il = finite(row5.get('inner_lower'))
    ou = finite(row5.get('outer_upper')); mid = finite(row5.get('mid'))
    px = finite(mr.get('close'))
    band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
    if not np.isfinite(px) or not np.isfinite(band_r) or band_r <= 0:
        return None
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


def first_micro_confirmation(m: pd.DataFrame, ready_ts: pd.Timestamp):
    q = m[(m.time >= ready_ts) & (m.time < ready_ts + pd.Timedelta(minutes=10))].copy()
    if q.empty:
        return None
    ok = (
        q['higher_low'].fillna(False)
        & q['higher_high_break'].fillna(False)
        & (num(q['gap_pos_ratio_3']) >= MICRO_RATIO)
        & (num(q['rsi_pos_ratio_3']) >= MICRO_RATIO)
        & (num(q['macd_gap_delta_1m']) > 0)
        & (num(q['rsi_slope_1m']) > 0)
    )
    hit = q[ok]
    return None if hit.empty else hit.iloc[0]


def build_candidates(sym, pf, micro, completed):
    z, m = slow.add_slow_turn_features(pf, micro)
    mid = num(z['mid_slope8'])
    gain = num(z[f'slope_gain_{SLOPE_LB}'])
    posr = num(z[f'slope_pos_ratio_{SLOPE_LB}'])
    gd = num(z.get('gap_delta'))
    rs = num(z.get('rsi_slope'))
    minute = z.time.dt.hour * 60 + z.time.dt.minute
    ready = (
        (minute >= 9 * 60 + 10)
        & (minute < base.NO_ENTRY_MINUTE)
        & (mid < 0)
        & (gain > 0)
        & (posr >= SLOPE_RATIO)
        & (gd > 0)
        & (rs > 0)
    )

    rows = []
    seen_day = set()
    comp = completed.copy().sort_values('time')
    comp['time'] = pd.to_datetime(comp['time'])
    for _, r in z[ready].iterrows():
        ts = pd.Timestamp(r.time)
        key = (n(sym), ts.date())
        if key in seen_day:
            continue
        mr = first_micro_confirmation(m, ts)
        if mr is None:
            continue
        q5 = comp[comp.time <= ts.floor('5min')]
        if q5.empty:
            continue
        ev = event_from_completed(sym, q5.iloc[-1], mr)
        if ev is None:
            continue
        seen_day.add(key)

        slope_gain = finite(r[f'slope_gain_{SLOPE_LB}'])
        per_bar_recovery = slope_gain / float(SLOPE_LB) if np.isfinite(slope_gain) else np.nan
        zero_cross_bars = abs(finite(r.mid_slope8)) / per_bar_recovery if np.isfinite(per_bar_recovery) and per_bar_recovery > 0 else np.inf
        rows.append(dict(
            symbol=n(sym), ready_time=ts, entry_time=pd.Timestamp(mr.time), entry_price=finite(mr.close),
            mid_slope8=finite(r.mid_slope8), slope_gain=slope_gain,
            recovery_per_bar=per_bar_recovery, zero_cross_bars=zero_cross_bars,
            gap_delta_5m=finite(r.get('gap_delta')), rsi_slope_5m=finite(r.get('rsi_slope')),
            gap_pos_ratio_1m=finite(mr.gap_pos_ratio_3), rsi_pos_ratio_1m=finite(mr.rsi_pos_ratio_3),
            event=ev,
        ))
    return pd.DataFrame(rows)


def event_stream(df):
    out = {}
    for _, r in df.iterrows():
        out.setdefault(pd.Timestamp(r.entry_time), []).append(r.event)
    return out


def attach_outcomes(cand, trades):
    if cand.empty or trades.empty:
        return cand.copy()
    t = trades.copy()
    t['entry_time'] = pd.to_datetime(t['entry_time'])
    t['symbol'] = t['symbol'].astype(str).str.zfill(6)
    t['net_pct'] = num(t['pnl_pct']) - FEE_RT_PCT
    out = cand.copy()
    out['entry_time'] = pd.to_datetime(out['entry_time'])
    out = out.merge(
        t[['symbol','entry_time','pnl_pct','net_pct','exit_time']].drop_duplicates(['symbol','entry_time']),
        on=['symbol','entry_time'], how='left'
    )
    out['win'] = num(out['net_pct']) > 0
    return out


def bucket_label(v, cuts, labels):
    if not np.isfinite(v):
        return 'NA'
    for c, lab in zip(cuts, labels):
        if v <= c:
            return lab
    return labels[-1]


def summarize(df):
    rows = []
    for axis in ['zero_cross_bucket', 'macd_bucket']:
        for k, g in df.groupby(axis, dropna=False):
            s = stat_from_pnl(g['pnl_pct'])
            rows.append(dict(axis=axis, bucket=str(k), **s,
                             median_zero_cross=float(num(g.zero_cross_bars).median()),
                             median_macd=float(num(g.gap_delta_5m).median())))
    return pd.DataFrame(rows)


def cross_tab(df):
    rows = []
    for (zb, mb), g in df.groupby(['zero_cross_bucket','macd_bucket']):
        s = stat_from_pnl(g['pnl_pct'])
        rows.append(dict(zero_cross_bucket=zb, macd_bucket=mb, **s))
    return pd.DataFrame(rows).sort_values(['net_sum','pf'], ascending=False)


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
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}

    parts = []
    for i, s in enumerate(raw, 1):
        print(f'[{i}/{len(raw)}] {s}', flush=True)
        pf, _ = st.load_or_build_cache(s, raw[s], cfg, completed[s])
        q = build_candidates(s, pf, micros[s], scored[s])
        if len(q):
            parts.append(q)

    cand = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    sev = event_stream(cand)
    trades = multi.simulate_multi(packed, sev, states, THRESHOLD)
    out = attach_outcomes(cand, trades)

    if out.empty:
        print('NO CANDIDATES')
        return

    out['zero_cross_bucket'] = [bucket_label(v, [1.5, 3, 6, 12, np.inf], ['<=1.5','1.5-3','3-6','6-12','>12']) for v in num(out.zero_cross_bars)]
    out['macd_bucket'] = [bucket_label(v, [10, 20, 40, 80, np.inf], ['<=10','10-20','20-40','40-80','>80']) for v in num(out.gap_delta_5m)]

    print('\n=== SLOW TURN ZERO-CROSS DISTANCE DIAGNOSTIC ===')
    print(f'Candidates={len(out)}  Trades={out.net_pct.notna().sum()}')
    print('No V20 rule changed. No threshold is selected here.')
    print('zero_cross_bars = abs(current negative mid_slope8) / average slope recovery per provisional bar')

    summary = summarize(out)
    print('\n=== ONE-DIMENSIONAL SUMMARY ===')
    print(summary.to_string(index=False))

    ct = cross_tab(out)
    print('\n=== ZERO-CROSS x MACD SUMMARY ===')
    print(ct.head(20).to_string(index=False))

    cols = ['symbol','ready_time','entry_time','entry_price','mid_slope8','recovery_per_bar','zero_cross_bars',
            'gap_delta_5m','rsi_slope_5m','net_pct','win']
    print('\n=== BEST NET CASES (max 25) ===')
    print(out[cols].sort_values('net_pct', ascending=False).head(MAX_PRINT).to_string(index=False))
    print('\n=== WORST NET CASES (max 25) ===')
    print(out[cols].sort_values('net_pct', ascending=True).head(MAX_PRINT).to_string(index=False))

    p1 = OUT_DIR / 'slow_turn_zero_cross_candidates.csv'
    p2 = OUT_DIR / 'slow_turn_zero_cross_summary.csv'
    p3 = OUT_DIR / 'slow_turn_zero_cross_cross_tab.csv'
    out.drop(columns=['event'], errors='ignore').to_csv(p1, index=False)
    summary.to_csv(p2, index=False)
    ct.to_csv(p3, index=False)
    print('\nWROTE', p1)
    print('WROTE', p2)
    print('WROTE', p3)


if __name__ == '__main__':
    main()
