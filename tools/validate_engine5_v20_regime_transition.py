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
import tools.validate_engine5_v20_rebound as rb
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight, to_5m
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_MIN = 52.0
REL_MIN = 1.45
MAX_ARM_MIN = 5

SLOPE_IMPROVE_RATIOS = [0.15, 0.25, 0.35]
ONE_M_LOOKBACKS = [3, 4, 5]
ONE_M_POS_RATIOS = [0.67, 0.75]
PRICE_REBOUND_PCTS = [0.10, 0.20, 0.30]

TARGETS = [
    ('950160', pd.Timestamp('2026-08-14').date()),
    ('950260', pd.Timestamp('2026-08-19').date()),
]


def finite(x):
    return h.finite(x)


def norm_sym(x):
    return str(x).zfill(6)


def build_provisional_5m(raw_bars, cfg):
    b = raw_bars.copy().sort_values('time').reset_index(drop=True)
    b['time'] = pd.to_datetime(b['time'])
    complete = to_5m(b)
    complete['time'] = pd.to_datetime(complete['time'])
    eng = DoubleBollingerEngine5(cfg)
    out = []

    for _, r1 in b.iterrows():
        ts = pd.Timestamp(r1.time)
        bucket_start = ts.floor('5min')
        bucket_end = bucket_start + pd.Timedelta(minutes=5)
        hist = complete[complete.time <= bucket_start].copy()
        cur = b[(b.time >= bucket_start) & (b.time <= ts)]
        if cur.empty:
            continue

        prov = pd.DataFrame([dict(
            time=bucket_end,
            open=float(cur.iloc[0].open),
            high=float(pd.to_numeric(cur.high, errors='coerce').max()),
            low=float(pd.to_numeric(cur.low, errors='coerce').min()),
            close=float(r1.close),
            volume=float(pd.to_numeric(cur.volume, errors='coerce').sum()),
        )])
        z = pd.concat([hist, prov], ignore_index=True).drop_duplicates('time', keep='last').sort_values('time')
        e = eng.enrich(z)
        if len(e) < 2:
            continue

        x = e.iloc[-1]
        prev = e.iloc[-2]
        cur_mid_slope = finite(x.mid_slope8)
        prev_mid_slope = finite(prev.mid_slope8)
        out.append(dict(
            time=ts,
            bucket_end=bucket_end,
            close=float(r1.close),
            macd=finite(x.macd),
            signal=finite(x.macd_signal),
            gap=finite(x.macd_gap),
            gap_delta=finite(x.macd_gap_delta),
            golden=bool(x.macd_golden_cross),
            macd_slope=finite(x.macd_slope),
            rsi=finite(x.rsi),
            rsi_slope=finite(x.rsi_slope),
            mid_slope8=cur_mid_slope,
            completed_prev_mid_slope8=prev_mid_slope,
            trend_up=bool(x.trend_up),
            entry_score=finite(x.entry_score),
        ))
    return pd.DataFrame(out)


def stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n > 0].sum()) if len(n) else 0.0
    gl = float(-n[n < 0].sum()) if len(n) else 0.0
    return dict(
        label=label,
        trades=len(n),
        net_wins=int((n > 0).sum()),
        net_losses=int((n <= 0).sum()),
        net_win_pct=float((n > 0).mean() * 100.0) if len(n) else 0.0,
        net_sum_pct=float(n.sum()) if len(n) else 0.0,
        net_avg_pct=float(n.mean()) if len(n) else 0.0,
        net_pf=(gp / gl) if gl > 0 else np.inf,
        max_net_loss_pct=float(n.min()) if len(n) else np.nan,
    )


def classify_transition_regime(p):
    prev = finite(p.completed_prev_mid_slope8)
    cur = finite(p.mid_slope8)
    if not (np.isfinite(prev) and np.isfinite(cur)):
        return 'NONE'
    if prev < 0 and cur <= 0:
        return 'DOWN_TURNING'
    if prev <= 0 < cur:
        return 'ZERO_CROSS_UP'
    if prev < 0 and cur > 0:
        return 'EARLY_UP'
    return 'NONE'


def transition_ready_5m(p, improve_ratio: float):
    regime = classify_transition_regime(p)
    if regime == 'NONE':
        return False, {}

    prev = finite(p.completed_prev_mid_slope8)
    cur = finite(p.mid_slope8)
    raw = finite(p.gap_delta)
    rel = finite(p.strength_rel)
    macd_slope = finite(p.macd_slope)
    rsi_slope = finite(p.rsi_slope)
    gap = finite(p.gap)
    rsi = finite(p.rsi)
    vals = [prev, cur, raw, rel, macd_slope, rsi_slope, gap, rsi]
    if not all(np.isfinite(x) for x in vals):
        return False, {}

    improve = cur - prev
    required = max(abs(prev) * float(improve_ratio), 1e-9)
    slope_turn = improve >= required
    momentum = raw >= RAW_MIN and rel >= REL_MIN and macd_slope > 0 and rsi_slope > 0
    ok = bool(slope_turn and momentum)
    return ok, dict(
        regime=regime,
        prev_slope=prev,
        cur_slope=cur,
        slope_improve=improve,
        required_improve=required,
        raw=raw,
        rel=rel,
        gap=gap,
        macd_slope_5m=macd_slope,
        rsi=rsi,
        rsi_slope_5m=rsi_slope,
        golden=bool(p.golden),
    )


def one_m_transition_confirm(m: pd.DataFrame, ts: pd.Timestamp, lookback: int,
                             min_pos_ratio: float, rebound_pct: float):
    q = m[m.time <= pd.Timestamp(ts)].tail(lookback).copy()
    if len(q) < lookback:
        return False, {}
    close = pd.to_numeric(q.close, errors='coerce').to_numpy(float)
    low = pd.to_numeric(q.low, errors='coerce').to_numpy(float)
    gaps = pd.to_numeric(q.macd_gap_1m, errors='coerce').to_numpy(float)
    rsis = pd.to_numeric(q.rsi_1m, errors='coerce').to_numpy(float)
    if not (np.isfinite(close).all() and np.isfinite(low).all() and np.isfinite(gaps).all() and np.isfinite(rsis).all()):
        return False, {}

    dg = np.diff(gaps)
    pos_ratio = float((dg > 0).mean()) if len(dg) else 0.0
    pos = float(dg[dg > 0].sum()) if np.any(dg > 0) else 0.0
    neg = float(-dg[dg < 0].sum()) if np.any(dg < 0) else 0.0
    retrace = neg / max(pos, 1e-9)
    last = q.iloc[-1]
    low_idx = int(np.argmin(low))
    local_low = float(low[low_idx])
    low_before_now = low_idx < len(q) - 1
    rebound = (float(close[-1]) / local_low - 1.0) * 100.0 if local_low > 0 else np.nan

    price_ok = bool(
        low_before_now
        and float(close[-1]) > float(close[-2])
        and float(close[-1]) > float(close[0])
        and np.isfinite(rebound)
        and rebound >= rebound_pct
    )
    momentum_ok = bool(
        gaps[-1] > gaps[0]
        and pos_ratio >= min_pos_ratio
        and retrace <= 0.35
        and finite(last.macd_slope_1m) > 0
        and finite(last.macd_gap_delta_1m) > 0
        and rsis[-1] > rsis[0]
        and finite(last.rsi_slope_1m) > 0
    )
    ok = bool(price_ok and momentum_ok)
    return ok, dict(
        local_low=local_low,
        low_time=pd.Timestamp(q.iloc[low_idx].time),
        rebound_pct=float(rebound),
        price_progress=float(close[-1] - close[0]),
        one_m_gap_start=float(gaps[0]),
        one_m_gap_end=float(gaps[-1]),
        one_m_gap_rise=float(gaps[-1] - gaps[0]),
        one_m_pos_ratio=pos_ratio,
        one_m_retrace=retrace,
        one_m_rsi_start=float(rsis[0]),
        one_m_rsi_end=float(rsis[-1]),
        last_gap_delta=finite(last.macd_gap_delta_1m),
        last_macd_slope=finite(last.macd_slope_1m),
        last_rsi_slope=finite(last.rsi_slope_1m),
    )


def build_transition_events(scored, micros, provisional,
                            improve_ratio: float, lookback: int,
                            min_pos_ratio: float, rebound_pct: float):
    events = {}
    diag = []
    seen_bucket = set()
    for sym, pf in provisional.items():
        sym = norm_sym(sym)
        if pf.empty or sym not in scored or sym not in micros:
            continue
        sf = scored[sym]
        m = micros[sym]
        armed = None
        armed_until = None
        for _, p in pf.iterrows():
            ts = pd.Timestamp(p.time)
            minute = ts.hour * 60 + ts.minute
            if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
                continue

            ready, meta5 = transition_ready_5m(p, improve_ratio)
            if ready:
                armed_until = ts + pd.Timedelta(minutes=MAX_ARM_MIN)
                armed = dict(
                    ready_time=ts,
                    bucket_end=pd.Timestamp(p.bucket_end),
                    ready_price=finite(p.close),
                    **meta5,
                )
            if armed is None or armed_until is None or ts > armed_until:
                continue

            confirmed, meta1 = one_m_transition_confirm(m, ts, lookback, min_pos_ratio, rebound_pct)
            if not confirmed:
                continue
            bucket_key = (sym, armed['bucket_end'])
            if bucket_key in seen_bucket:
                continue
            q5 = sf[sf.time <= ts.floor('5min')]
            if q5.empty:
                continue
            row5 = q5.iloc[-1]
            ev = h.event_from_5m_row(sym, row5, ts, finite(p.close))
            if ev is None:
                continue

            seen_bucket.add(bucket_key)
            events.setdefault(ts, []).append(ev)
            diag.append(dict(
                symbol=sym,
                trigger_time=ts,
                trigger_price=finite(p.close),
                delay_min=(ts - armed['ready_time']).total_seconds() / 60.0,
                improve_ratio=improve_ratio,
                lookback=lookback,
                min_pos_ratio=min_pos_ratio,
                min_rebound_pct=rebound_pct,
                **armed,
                **meta1,
            ))
            armed = None
            armed_until = None
    return events, pd.DataFrame(diag)


def merge_events(base_events, extra_events):
    out = {pd.Timestamp(ts): list(items) for ts, items in base_events.items()}
    for ts, items in extra_events.items():
        out.setdefault(pd.Timestamp(ts), []).extend(items)
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {norm_sym(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored0 = reweight(f10, cfg, 0.0)
    scored = {norm_sym(s): f for s, f in scored0.items()}
    strength_frames = {sym: ms.add_strength(f) for sym, f in scored.items()}

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {norm_sym(s): h.build_micro(b, cfg) for s, b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)
    ev20, _ = ms.filter_events(ev18, strength_frames, raw_min=RAW_MIN, rel_min=REL_MIN)
    base_trades = multi.simulate_multi(packed, ev20, states, THRESHOLD)

    provisional = {}
    for s, b in raw.items():
        sym = norm_sym(s)
        pf = build_provisional_5m(b, cfg)
        provisional[sym] = rb.add_provisional_strength(pf, strength_frames[sym])

    print('=== V20 REGIME TRANSITION VALIDATION ===')
    print('BASE: current V20 unchanged for established uptrend entries.')
    print('EXTRA: down/flat -> strong upward transition only; no trend_up prerequisite.')
    print(f'5m qualification: RAW>={RAW_MIN:g}, REL>={REL_MIN:g}, slope improvement, MACD slope>0, RSI slope>0.')
    print('1m confirmation: actual price rebound + coherent MACD/RSI continuation; mild pullback allowed.')
    print(pd.DataFrame([stats('V20_BASE', base_trades)]).to_string(index=False))

    rows = []
    all_diag = []
    for ir in SLOPE_IMPROVE_RATIOS:
        for lb in ONE_M_LOOKBACKS:
            for pr in ONE_M_POS_RATIOS:
                for rp in PRICE_REBOUND_PCTS:
                    extra, d = build_transition_events(scored, micros, provisional, ir, lb, pr, rp)
                    merged = merge_events(ev20, extra)
                    t_extra = multi.simulate_multi(packed, extra, states, THRESHOLD)
                    t_merged = multi.simulate_multi(packed, merged, states, THRESHOLD)
                    label = f'TRANS_IR{ir:.2f}_LB{lb}_POS{pr:.2f}_R{rp:.2f}'
                    sm = stats(label, t_merged)
                    se = stats(label + '_EXTRA_ONLY', t_extra)
                    sm.update(
                        improve_ratio=ir,
                        lookback=lb,
                        min_pos_ratio=pr,
                        min_rebound_pct=rp,
                        transition_triggers=len(d),
                        extra_trades=se['trades'],
                        extra_win_pct=se['net_win_pct'],
                        extra_net_sum_pct=se['net_sum_pct'],
                        extra_pf=se['net_pf'],
                    )
                    rows.append(sm)
                    if len(d):
                        dd = d.copy()
                        dd.insert(0, 'label', label)
                        all_diag.append(dd)

    summary = pd.DataFrame(rows).sort_values(['net_sum_pct', 'net_pf', 'net_win_pct'], ascending=False)
    print('\n=== MERGED SWEEP SUMMARY ===')
    print(summary.to_string(index=False))

    diag = pd.concat(all_diag, ignore_index=True) if all_diag else pd.DataFrame()
    print('\n=== TARGET TRANSITION TRIGGERS: 950160 8/14 + 950260 8/19 ===')
    if len(diag):
        dt = pd.to_datetime(diag.trigger_time)
        mask = pd.Series(False, index=diag.index)
        for sym, day in TARGETS:
            mask |= (diag.symbol == sym) & (dt.dt.date == day)
        target = diag[mask].copy().sort_values(['symbol', 'trigger_time', 'label'])
        cols = [
            'label','symbol','regime','ready_time','trigger_time','delay_min',
            'ready_price','trigger_price','prev_slope','cur_slope','slope_improve',
            'raw','rel','gap','golden','rsi','rsi_slope_5m',
            'low_time','local_low','rebound_pct','price_progress',
            'one_m_gap_start','one_m_gap_end','one_m_gap_rise','one_m_pos_ratio',
            'one_m_retrace','one_m_rsi_start','one_m_rsi_end',
            'last_gap_delta','last_macd_slope','last_rsi_slope'
        ]
        print(target[[c for c in cols if c in target.columns]].to_string(index=False) if len(target) else 'NONE')
        target.to_csv(OUT_DIR / 'v20_regime_transition_targets.csv', index=False)
    else:
        print('NONE')
        pd.DataFrame().to_csv(OUT_DIR / 'v20_regime_transition_targets.csv', index=False)

    summary.to_csv(OUT_DIR / 'v20_regime_transition_summary.csv', index=False)
    if len(diag):
        diag.to_csv(OUT_DIR / 'v20_regime_transition_all_diag.csv', index=False)
    else:
        pd.DataFrame().to_csv(OUT_DIR / 'v20_regime_transition_all_diag.csv', index=False)

    print('\nWROTE', OUT_DIR / 'v20_regime_transition_summary.csv')
    print('WROTE', OUT_DIR / 'v20_regime_transition_targets.csv')
    print('WROTE', OUT_DIR / 'v20_regime_transition_all_diag.csv')


if __name__ == '__main__':
    main()
