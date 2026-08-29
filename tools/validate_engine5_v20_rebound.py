from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_live_5m_1m as live
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight, to_5m
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_MIN = 52.0
REL_MIN = 1.45
REL_LOOKBACK = 8
MAX_WAIT_MIN = 4
LOOKBACKS = [3, 4, 5]
POS_RATIOS = [0.60, 0.67, 0.75]
SLOPE_IMPROVE_RATIOS = [0.10, 0.20, 0.30]
TARGETS = [
    ('950260', pd.Timestamp('2026-08-21').date()),
    ('950260', pd.Timestamp('2026-08-19').date()),
]


def finite(x):
    return h.finite(x)


def add_completed_strength(frame: pd.DataFrame) -> pd.DataFrame:
    f = frame.copy().sort_values('time').reset_index(drop=True)
    d = pd.to_numeric(f['macd_gap_delta'], errors='coerce')
    b = d.abs().shift(1).rolling(REL_LOOKBACK, min_periods=4).median()
    f['strength_baseline'] = b
    return f


def add_provisional_strength(pf: pd.DataFrame, completed_strength: pd.DataFrame) -> pd.DataFrame:
    if pf.empty:
        return pf.copy()
    z = pf.copy().sort_values('time').reset_index(drop=True)
    baselines = []
    rels = []
    prev_slopes = []
    for _, p in z.iterrows():
        ts = pd.Timestamp(p.time)
        q = completed_strength[completed_strength.time <= ts.floor('5min')]
        if q.empty:
            baseline = np.nan
            prev_slope = np.nan
        else:
            r = q.iloc[-1]
            baseline = finite(r.get('strength_baseline', np.nan))
            prev_slope = finite(r.get('mid_slope8', np.nan))
        raw = finite(p.gap_delta)
        rel = raw / baseline if np.isfinite(raw) and raw > 0 and np.isfinite(baseline) and baseline > 0 else np.nan
        baselines.append(baseline)
        rels.append(rel)
        prev_slopes.append(prev_slope)
    z['strength_baseline'] = baselines
    z['strength_rel'] = rels
    z['completed_prev_mid_slope8'] = prev_slopes
    return z


def rebound_5m_ok(p, improve_ratio: float):
    prev = finite(p.completed_prev_mid_slope8)
    cur = finite(p.mid_slope8)
    raw = finite(p.gap_delta)
    rel = finite(p.strength_rel)
    macd_slope = finite(p.macd_slope)
    if not all(np.isfinite(x) for x in [prev, cur, raw, rel, macd_slope]):
        return False, {}
    # Dedicated rebound path: the stock was structurally falling on the last completed 5m bar.
    if prev >= 0:
        return False, {}
    improve = cur - prev
    required = max(abs(prev) * float(improve_ratio), 1e-9)
    slope_turn = improve >= required
    power = raw >= RAW_MIN and rel >= REL_MIN and macd_slope > 0
    ok = bool(slope_turn and power)
    return ok, dict(prev_slope=prev, cur_slope=cur, slope_improve=improve,
                    required_improve=required, raw=raw, rel=rel,
                    golden=bool(p.golden), gap=finite(p.gap))


def rebound_1m_ok(m: pd.DataFrame, ts: pd.Timestamp, lookback: int, min_pos_ratio: float):
    q = m[m.time <= pd.Timestamp(ts)].tail(lookback)
    if len(q) < lookback:
        return False, {}
    gaps = pd.to_numeric(q.macd_gap_1m, errors='coerce').to_numpy(float)
    prices = pd.to_numeric(q.close, errors='coerce').to_numpy(float)
    if not np.isfinite(gaps).all() or not np.isfinite(prices).all():
        return False, {}
    dg = np.diff(gaps)
    dp = np.diff(prices)
    gap_pos_ratio = float((dg > 0).mean())
    price_pos_ratio = float((dp > 0).mean())
    total_gap_rise = float(gaps[-1] - gaps[0])
    total_price_rise = float(prices[-1] - prices[0])
    neg = float(-dg[dg < 0].sum()) if np.any(dg < 0) else 0.0
    pos = float(dg[dg > 0].sum()) if np.any(dg > 0) else 0.0
    retrace = neg / max(pos, 1e-9)
    last = q.iloc[-1]
    # Do not require every 1m bar positive. A modest pullback is allowed, but momentum and price
    # must both have progressed from the start of the confirmation window.
    ok = bool(
        total_gap_rise > 0
        and total_price_rise > 0
        and gap_pos_ratio >= min_pos_ratio
        and price_pos_ratio >= 0.50
        and retrace <= 0.35
        and finite(last.macd_slope_1m) > 0
        and finite(last.macd_gap_delta_1m) > 0
        and finite(last.rsi_slope_1m) > 0
    )
    return ok, dict(one_m_gap_start=gaps[0], one_m_gap_end=gaps[-1],
                    one_m_gap_rise=total_gap_rise, one_m_gap_pos_ratio=gap_pos_ratio,
                    one_m_price_rise=total_price_rise, one_m_price_pos_ratio=price_pos_ratio,
                    one_m_retrace=retrace, one_m_rsi=finite(last.rsi_1m),
                    one_m_rsi_slope=finite(last.rsi_slope_1m))


def make_event(sym: str, completed_row, ts: pd.Timestamp, price: float):
    # Rebound entries intentionally bypass the old entry_score/trend_up gate.
    # We only reuse DBB geometry for simulator risk/exit tuple compatibility.
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
        str(sym).zfill(6), float(price), float(score),
        finite(completed_row.get('macd_slope_spread_strength', np.nan)),
        finite(completed_row.get('rsi_slope_strength', np.nan)),
        float(band_r), float(band_r), iu, il, ou, mid, extended, False,
    )


def build_rebound_events(scored, micros, provisional, lookback, min_pos_ratio, improve_ratio):
    events = {}
    diag = []
    seen_buckets = set()
    for sym, pf in provisional.items():
        sym = str(sym).zfill(6)
        if pf.empty or sym not in micros or sym not in scored:
            continue
        m = micros[sym]
        sf = scored[sym]
        armed_until = None
        arm = None
        for _, p in pf.iterrows():
            ts = pd.Timestamp(p.time)
            minute = ts.hour * 60 + ts.minute
            if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
                continue
            ok5, meta5 = rebound_5m_ok(p, improve_ratio)
            if ok5:
                armed_until = ts + pd.Timedelta(minutes=MAX_WAIT_MIN)
                arm = dict(arm_time=ts, bucket_end=pd.Timestamp(p.bucket_end), **meta5)
            if armed_until is None or ts > armed_until or arm is None:
                continue
            ok1, meta1 = rebound_1m_ok(m, ts, lookback, min_pos_ratio)
            if not ok1:
                continue
            bucket_key = (sym, arm['bucket_end'])
            if bucket_key in seen_buckets:
                continue
            # Latest fully completed 5m row only; current forming bar is never treated as complete.
            q5 = sf[sf.time <= ts.floor('5min')]
            if q5.empty:
                continue
            row5 = q5.iloc[-1]
            ev = make_event(sym, row5, ts, finite(p.close))
            if ev is None:
                continue
            seen_buckets.add(bucket_key)
            events.setdefault(ts, []).append(ev)
            diag.append(dict(symbol=sym, trigger_time=ts, trigger_price=finite(p.close),
                             lookback=lookback, min_pos_ratio=min_pos_ratio,
                             improve_ratio=improve_ratio, **arm, **meta1))
            armed_until = None
            arm = None
    return events, pd.DataFrame(diag)


def stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(label=label, trades=len(n), net_wins=int((n > 0).sum()),
                net_win_pct=float((n > 0).mean() * 100) if len(n) else 0.0,
                net_sum_pct=float(n.sum()) if len(n) else 0.0,
                net_avg_pct=float(n.mean()) if len(n) else 0.0,
                net_pf=gp / gl if gl > 0 else np.inf,
                gross_sum_pct=float(g.sum()) if len(g) else 0.0,
                max_net_loss_pct=float(n.min()) if len(n) else np.nan)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {str(s).zfill(6): v10._refine_entry_frame(f) for s, f in frames.items()}
    scored0 = reweight(f10, cfg, 0.0)
    scored = {str(s).zfill(6): add_completed_strength(f) for s, f in scored0.items()}
    micros = {str(s).zfill(6): h.build_micro(b, cfg) for s, b in raw.items()}
    provisional = {}
    for s, b in raw.items():
        sym = str(s).zfill(6)
        pf = live.build_provisional_5m(b, cfg)
        provisional[sym] = add_provisional_strength(pf, scored[sym])

    rows = []
    all_diag = []
    for ir in SLOPE_IMPROVE_RATIOS:
        for lb in LOOKBACKS:
            for pr in POS_RATIOS:
                ev, d = build_rebound_events(scored, micros, provisional, lb, pr, ir)
                t = multi.simulate_multi(packed, ev, states, THRESHOLD)
                label = f'REBOUND_IR{ir:.2f}_LB{lb}_POS{pr:.2f}'
                s = stats(label, t)
                s.update(improve_ratio=ir, lookback=lb, min_pos_ratio=pr, triggered=len(d))
                rows.append(s)
                if len(d):
                    d.insert(0, 'label', label)
                    all_diag.append(d)

    summary = pd.DataFrame(rows).sort_values(['net_sum_pct', 'net_pf', 'net_win_pct'], ascending=False)
    print('=== V20 REBOUND / V-SHAPE PATH ===')
    print(f'5m rebound power fixed: RAW>={RAW_MIN:g}, REL>={REL_MIN:g}x. Prior completed 5m slope must be negative; forming 5m slope must improve.')
    print('1m confirms continuity; old trend_up/entry_score gate is bypassed only for this rebound path.')
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    diag = pd.concat(all_diag, ignore_index=True) if all_diag else pd.DataFrame()
    print('\n=== TARGET DIAGNOSTICS ===')
    if len(diag):
        target = diag[(diag.symbol == '950260') & (pd.to_datetime(diag.trigger_time).dt.date.isin([x[1] for x in TARGETS]))].copy()
        print(target.sort_values(['trigger_time', 'label']).to_string(index=False) if len(target) else 'NONE')
        target.to_csv(OUT_DIR / 'v20_rebound_950260_diag.csv', index=False)
    else:
        print('NONE')
        pd.DataFrame().to_csv(OUT_DIR / 'v20_rebound_950260_diag.csv', index=False)

    # Also print raw provisional mechanics around the two regression windows even if no trade fires.
    for day in [pd.Timestamp('2026-08-19').date(), pd.Timestamp('2026-08-21').date()]:
        pf = provisional['950260'].copy()
        q = pf[pd.to_datetime(pf.time).dt.date == day]
        if day == pd.Timestamp('2026-08-19').date():
            q = q[(q.time.dt.strftime('%H:%M') >= '09:20') & (q.time.dt.strftime('%H:%M') <= '09:40')]
        else:
            q = q[(q.time.dt.strftime('%H:%M') >= '09:50') & (q.time.dt.strftime('%H:%M') <= '12:25')]
        print(f'\n=== 950260 PROVISIONAL REBOUND MECHANICS {day} ===')
        cols = ['time','close','gap','gap_delta','strength_rel','completed_prev_mid_slope8','mid_slope8','mid_slope_improve','macd_slope','golden']
        print(q[[c for c in cols if c in q.columns]].to_string(index=False) if len(q) else 'NONE')

    summary.to_csv(OUT_DIR / 'v20_rebound_summary.csv', index=False)
    print('\nWROTE', OUT_DIR / 'v20_rebound_summary.csv')
    print('WROTE', OUT_DIR / 'v20_rebound_950260_diag.csv')


if __name__ == '__main__':
    main()
