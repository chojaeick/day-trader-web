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
import tools.validate_engine5_v20_rebound as rb
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
THRESHOLD = 50
FEE_RT_PCT = 0.25
RAW_MIN = 52.0
REL_MIN = 1.45
MAX_READY_MIN = 5

# Reversal is two-stage:
#   READY: falling 5m structure is bending up + MACD/RSI improve strongly.
#   BUY:   actual 1m price turns upward after a local low while MACD/RSI improvement persists.
LOOKBACKS = [3, 4, 5]
PRICE_REBOUND_PCTS = [0.10, 0.20, 0.30, 0.50]
SLOPE_IMPROVE_RATIOS = [0.10, 0.20, 0.30]

TARGETS = [
    ('950260', pd.Timestamp('2026-08-19').date()),
    ('950260', pd.Timestamp('2026-08-21').date()),
]


def finite(x):
    return h.finite(x)


def reversal_ready_5m(p, improve_ratio: float):
    prev = finite(p.completed_prev_mid_slope8)
    cur = finite(p.mid_slope8)
    raw = finite(p.gap_delta)
    rel = finite(p.strength_rel)
    macd_slope = finite(p.macd_slope)
    rsi_slope = finite(p.rsi_slope)
    if not all(np.isfinite(x) for x in [prev, cur, raw, rel, macd_slope, rsi_slope]):
        return False, {}

    # Must really come from a falling structure. We do not call an ordinary uptrend a reversal.
    if prev >= 0:
        return False, {}

    improve = cur - prev
    required = max(abs(prev) * float(improve_ratio), 1e-9)
    slope_turning = improve >= required
    momentum_ready = (
        raw >= RAW_MIN
        and rel >= REL_MIN
        and macd_slope > 0
        and rsi_slope > 0
    )
    ok = bool(slope_turning and momentum_ready)
    return ok, dict(
        prev_slope=prev,
        cur_slope=cur,
        slope_improve=improve,
        required_improve=required,
        raw=raw,
        rel=rel,
        macd_slope=macd_slope,
        rsi_slope_5m=rsi_slope,
        golden=bool(p.golden),
        gap=finite(p.gap),
    )


def price_confirm_1m(m: pd.DataFrame, ts: pd.Timestamp, lookback: int, rebound_pct: float):
    q = m[m.time <= pd.Timestamp(ts)].tail(lookback).copy()
    if len(q) < lookback:
        return False, {}

    close = pd.to_numeric(q.close, errors='coerce').to_numpy(float)
    low = pd.to_numeric(q.low, errors='coerce').to_numpy(float)
    gaps = pd.to_numeric(q.macd_gap_1m, errors='coerce').to_numpy(float)
    rsis = pd.to_numeric(q.rsi_1m, errors='coerce').to_numpy(float)
    if not (np.isfinite(close).all() and np.isfinite(low).all() and np.isfinite(gaps).all() and np.isfinite(rsis).all()):
        return False, {}

    last = q.iloc[-1]
    prev = q.iloc[-2]

    # The low must have happened before the current bar. If the newest bar is making the low,
    # price has not visibly turned yet.
    low_idx = int(np.argmin(low))
    local_low = float(low[low_idx])
    low_before_now = low_idx < len(q) - 1

    rebound = (float(close[-1]) / local_low - 1.0) * 100.0 if local_low > 0 else np.nan
    current_up = float(close[-1]) > float(close[-2])

    # Actual price confirmation: not only an oscillator turn. Price must be advancing from the low.
    # We intentionally do not require a breakout of the whole window high; that would be too late
    # for a sharp V reversal.
    price_ok = bool(low_before_now and current_up and np.isfinite(rebound) and rebound >= rebound_pct)

    # Momentum must still be improving at the moment price confirms.
    macd_ok = bool(
        finite(last.macd_slope_1m) > 0
        and finite(last.macd_gap_delta_1m) > 0
        and gaps[-1] > gaps[0]
    )
    rsi_ok = bool(
        finite(last.rsi_slope_1m) > 0
        and rsis[-1] > rsis[0]
    )

    # Mild one-bar noise is allowed, but the short window should show net price progress.
    price_progress = float(close[-1] - close[0])
    ok = bool(price_ok and macd_ok and rsi_ok and price_progress > 0)

    return ok, dict(
        local_low=local_low,
        low_time=pd.Timestamp(q.iloc[low_idx].time),
        rebound_pct=float(rebound),
        current_up=current_up,
        price_progress=price_progress,
        one_m_gap_start=float(gaps[0]),
        one_m_gap_end=float(gaps[-1]),
        one_m_rsi_start=float(rsis[0]),
        one_m_rsi_end=float(rsis[-1]),
        last_macd_slope=finite(last.macd_slope_1m),
        last_gap_delta=finite(last.macd_gap_delta_1m),
        last_rsi_slope=finite(last.rsi_slope_1m),
        prev_close=finite(prev.close),
        close=finite(last.close),
    )


def build_events(scored, micros, provisional, lookback: int, rebound_pct: float, improve_ratio: float):
    events = {}
    diag = []
    seen_buckets = set()

    for sym, pf in provisional.items():
        sym = str(sym).zfill(6)
        if pf.empty or sym not in micros or sym not in scored:
            continue
        m = micros[sym]
        sf = scored[sym]
        ready_until = None
        ready = None

        for _, p in pf.iterrows():
            ts = pd.Timestamp(p.time)
            minute = ts.hour * 60 + ts.minute
            if minute < 9 * 60 + 10 or minute >= base.NO_ENTRY_MINUTE:
                continue

            ok_ready, meta5 = reversal_ready_5m(p, improve_ratio)
            if ok_ready:
                ready_until = ts + pd.Timedelta(minutes=MAX_READY_MIN)
                ready = dict(
                    ready_time=ts,
                    bucket_end=pd.Timestamp(p.bucket_end),
                    ready_price=finite(p.close),
                    **meta5,
                )

            if ready is None or ready_until is None or ts > ready_until:
                continue

            ok_price, meta1 = price_confirm_1m(m, ts, lookback, rebound_pct)
            if not ok_price:
                continue

            bucket_key = (sym, ready['bucket_end'])
            if bucket_key in seen_buckets:
                continue

            q5 = sf[sf.time <= ts.floor('5min')]
            if q5.empty:
                continue
            row5 = q5.iloc[-1]
            ev = rb.make_event(sym, row5, ts, finite(p.close))
            if ev is None:
                continue

            seen_buckets.add(bucket_key)
            events.setdefault(ts, []).append(ev)
            diag.append(dict(
                symbol=sym,
                trigger_time=ts,
                trigger_price=finite(p.close),
                delay_min=(ts - ready['ready_time']).total_seconds() / 60.0,
                lookback=lookback,
                min_rebound_pct=rebound_pct,
                improve_ratio=improve_ratio,
                **ready,
                **meta1,
            ))
            ready = None
            ready_until = None

    return events, pd.DataFrame(diag)


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
        net_win_pct=float((n > 0).mean() * 100) if len(n) else 0.0,
        net_sum_pct=float(n.sum()) if len(n) else 0.0,
        net_avg_pct=float(n.mean()) if len(n) else 0.0,
        net_pf=gp / gl if gl > 0 else np.inf,
        gross_sum_pct=float(g.sum()) if len(g) else 0.0,
        max_net_loss_pct=float(n.min()) if len(n) else np.nan,
    )


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
    scored = {str(s).zfill(6): rb.add_completed_strength(f) for s, f in scored0.items()}
    micros = {str(s).zfill(6): h.build_micro(b, cfg) for s, b in raw.items()}

    provisional = {}
    for s, b in raw.items():
        sym = str(s).zfill(6)
        pf = live.build_provisional_5m(b, cfg)
        provisional[sym] = rb.add_provisional_strength(pf, scored[sym])

    rows = []
    all_diag = []
    for ir in SLOPE_IMPROVE_RATIOS:
        for lb in LOOKBACKS:
            for rp in PRICE_REBOUND_PCTS:
                ev, d = build_events(scored, micros, provisional, lb, rp, ir)
                t = multi.simulate_multi(packed, ev, states, THRESHOLD)
                label = f'REVERSAL_IR{ir:.2f}_LB{lb}_R{rp:.2f}'
                s = stats(label, t)
                s.update(improve_ratio=ir, lookback=lb, min_rebound_pct=rp, triggered=len(d))
                rows.append(s)
                if len(d):
                    d.insert(0, 'label', label)
                    all_diag.append(d)

    summary = pd.DataFrame(rows).sort_values(['net_sum_pct', 'net_pf', 'net_win_pct'], ascending=False)
    print('=== V20 REVERSAL: MOMENTUM READY -> ACTUAL PRICE RISE -> BUY ===')
    print(f'5m READY: prior slope<0, slope improving, MACD raw>={RAW_MIN:g}, rel>={REL_MIN:g}x, MACD slope>0, RSI slope>0.')
    print('1m BUY: local low already formed, current close>previous close, price rebounds from low by threshold, MACD/RSI still improving.')
    print('No future 5m data; the current forming 5m bar is evaluated causally each minute.')
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    diag = pd.concat(all_diag, ignore_index=True) if all_diag else pd.DataFrame()
    print('\n=== 950260 TARGET DIAGNOSTICS ===')
    if len(diag):
        target_days = [x[1] for x in TARGETS]
        target = diag[(diag.symbol == '950260') & (pd.to_datetime(diag.trigger_time).dt.date.isin(target_days))].copy()
        cols = [
            'label','symbol','ready_time','trigger_time','delay_min','ready_price','trigger_price',
            'prev_slope','cur_slope','slope_improve','raw','rel','rsi_slope_5m',
            'low_time','local_low','rebound_pct','price_progress',
            'one_m_gap_start','one_m_gap_end','one_m_rsi_start','one_m_rsi_end',
            'last_macd_slope','last_gap_delta','last_rsi_slope'
        ]
        print(target[[c for c in cols if c in target.columns]].sort_values(['trigger_time','label']).to_string(index=False) if len(target) else 'NONE')
        target.to_csv(OUT_DIR / 'v20_reversal_price_confirm_950260.csv', index=False)
    else:
        print('NONE')
        pd.DataFrame().to_csv(OUT_DIR / 'v20_reversal_price_confirm_950260.csv', index=False)

    summary.to_csv(OUT_DIR / 'v20_reversal_price_confirm_summary.csv', index=False)
    if len(diag):
        diag.to_csv(OUT_DIR / 'v20_reversal_price_confirm_all_diag.csv', index=False)
    else:
        pd.DataFrame().to_csv(OUT_DIR / 'v20_reversal_price_confirm_all_diag.csv', index=False)

    print('\nWROTE', OUT_DIR / 'v20_reversal_price_confirm_summary.csv')
    print('WROTE', OUT_DIR / 'v20_reversal_price_confirm_950260.csv')
    print('WROTE', OUT_DIR / 'v20_reversal_price_confirm_all_diag.csv')


if __name__ == '__main__':
    main()
