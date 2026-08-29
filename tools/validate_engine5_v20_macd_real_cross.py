from __future__ import annotations

from dataclasses import replace

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
import tools.validate_engine5_v19_prebuy_5m_1m_confirm as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
FEE_RT_PCT = 0.25
CROSS_BARS = 4
MAX_AGE_MIN = 2
TARGET = ('950260', pd.Timestamp('2026-08-21 10:00:00+09:00'))

# MACD-signal gaps are normalized by current price so symbols are comparable.
# A REAL cross must look like: negative -> less negative -> positive -> more positive.
# Example in raw MACD units: -50 -> -20 -> +10 -> +30.
# We sweep the minimum total separation and minimum per-step improvement.
TOTAL_SWING_LEVELS = [0.03, 0.05, 0.08, 0.10, 0.15]
MIN_STEP_LEVELS = [0.005, 0.010, 0.015, 0.020]


def finite(x):
    return h.finite(x)


def norm_pct(x, close):
    x = finite(x); close = finite(close)
    if not np.isfinite(x) or not np.isfinite(close) or close <= 0:
        return np.nan
    return x / close * 100.0


def gap_pct(row):
    if row is None:
        return np.nan
    return norm_pct(finite(row.get('macd_1m')) - finite(row.get('signal_1m')), row.get('close'))


def last4(m, ts):
    q = m[m.time <= pd.Timestamp(ts)].tail(CROSS_BARS).copy()
    return q if len(q) == CROSS_BARS else pd.DataFrame()


def base_direction_ok(row):
    if row is None:
        return False
    vals = [row.get('macd_1m'), row.get('signal_1m'), row.get('macd_slope_1m'),
            row.get('macd_gap_delta_1m'), row.get('rsi_slope_1m')]
    if not all(np.isfinite(finite(x)) for x in vals):
        return False
    return bool(
        finite(row['macd_1m']) > finite(row['signal_1m'])
        and finite(row['macd_slope_1m']) > 0
        and finite(row['macd_gap_delta_1m']) > 0
        and finite(row['rsi_slope_1m']) > 0
    )


def strict_cross(m, row, total_swing_pct, min_step_pct):
    """Require a genuine 4-bar MACD-signal separation sequence.

    Pattern must be causal and already completed at entry time:
      g0 < 0, g1 < 0, g2 > 0, g3 > g2 > 0
      g0 < g1 < g2 < g3
    So a weak rub such as -0.002, -0.001, +0.001, +0.0015 is rejected.
    In addition, every gap improvement must exceed min_step_pct and the total
    g3-g0 swing must exceed total_swing_pct (all normalized by price).
    """
    if row is None or not base_direction_ok(row):
        return False, 'BASE_DIRECTION_FAIL', None

    q = last4(m, row.time)
    if q.empty:
        return False, 'NO_LAST4', None

    gaps = np.array([gap_pct(r) for _, r in q.iterrows()], dtype=float)
    if not np.all(np.isfinite(gaps)):
        return False, 'NONFINITE_GAPS', None

    g0, g1, g2, g3 = gaps
    steps = np.diff(gaps)
    total = g3 - g0

    # Must actually cross from below to above and keep widening after the cross.
    sign_shape = bool(g0 < 0 and g1 < 0 and g2 > 0 and g3 > 0)
    monotonic = bool(np.all(steps > 0))
    step_power = bool(np.all(steps >= min_step_pct))
    total_power = bool(total >= total_swing_pct)

    metrics = {
        'g0': g0, 'g1': g1, 'g2': g2, 'g3': g3,
        'step1': steps[0], 'step2': steps[1], 'step3': steps[2],
        'total_swing': total,
    }

    if not sign_shape:
        return False, 'NOT_TRUE_NEG_TO_POS_CROSS', metrics
    if not monotonic:
        return False, 'CROSS_NOT_MONOTONIC', metrics
    if not step_power:
        return False, 'CROSS_STEPS_TOO_WEAK', metrics
    if not total_power:
        return False, 'CROSS_TOTAL_SWING_TOO_WEAK', metrics
    return True, 'STRICT_REAL_CROSS', metrics


def first_strict_cross(m, end_ts, total_swing_pct, min_step_pct, lookback_min=12):
    q = m[(m.time >= pd.Timestamp(end_ts) - pd.Timedelta(minutes=lookback_min)) &
          (m.time <= pd.Timestamp(end_ts))]
    for _, r in q.iterrows():
        ok, reason, metrics = strict_cross(m, r, total_swing_pct, min_step_pct)
        if ok:
            return pd.Timestamp(r.time), reason, metrics
    return pd.NaT, None, None


def build_fast(scored, micros, raw, total_swing_pct, min_step_pct):
    first_dates = v19.first_trading_dates(raw)
    events, diag, seen = {}, [], set()

    for sym0, f in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        prev_pre = False

        for _, row5 in f.copy().sort_values('time').reset_index(drop=True).iterrows():
            ts = pd.Timestamp(row5.time)
            minute = ts.hour * 60 + ts.minute
            pre = bool(9*60+10 <= minute < base.NO_ENTRY_MINUTE and v19.prebuy_5m(row5))
            birth = pre and not prev_pre
            prev_pre = pre
            if not birth or ts.date() == first_dates.get(sym):
                continue

            # If a genuine cross already completed before 5m READY, do not chase it later.
            old_cross, old_reason, old_metrics = first_strict_cross(
                m, ts - pd.Timedelta(minutes=1), total_swing_pct, min_step_pct
            )
            if pd.notna(old_cross):
                diag.append({
                    'symbol': sym, 'ready_time': ts, 'status': 'STALE_PRIOR_REAL_CROSS',
                    'cross_time': old_cross, 'cross_reason': old_reason,
                    'g0': old_metrics['g0'], 'g1': old_metrics['g1'],
                    'g2': old_metrics['g2'], 'g3': old_metrics['g3'],
                })
                continue

            q = m[(m.time >= ts) & (m.time <= ts + pd.Timedelta(minutes=MAX_AGE_MIN))]
            chosen = None; chosen_metrics = None
            for _, r in q.iterrows():
                ok, reason, metrics = strict_cross(m, r, total_swing_pct, min_step_pct)
                if ok:
                    chosen = r; chosen_metrics = metrics; break

            if chosen is None:
                diag.append({'symbol':sym,'ready_time':ts,'status':'NO_STRICT_REAL_CROSS',
                             'cross_time':pd.NaT,'cross_reason':None})
                continue

            dts = pd.Timestamp(chosen.time)
            ev = h.event_from_5m_row(sym, row5, dts, finite(chosen.close))
            key = (sym, dts)
            if ev is not None and key not in seen:
                seen.add(key)
                events.setdefault(dts, []).append(ev)
                diag.append({
                    'symbol': sym, 'ready_time': ts, 'status': 'TRIGGERED',
                    'cross_time': dts, 'cross_reason': 'STRICT_REAL_CROSS',
                    **chosen_metrics,
                })
    return events, pd.DataFrame(diag)


def veto_base(ev18, micros, total_swing_pct, min_step_pct):
    out, blocked = {}, []
    for ts in sorted(ev18):
        for c in ev18[ts]:
            sym = str(c[0]).zfill(6)
            m = micros[sym]
            now = h.micro_row_at(m, ts)
            cross, cross_reason, metrics = first_strict_cross(
                m, ts, total_swing_pct, min_step_pct
            )
            age = np.nan if pd.isna(cross) else (pd.Timestamp(ts)-cross).total_seconds()/60.0
            fresh = pd.notna(cross) and 0 <= age <= MAX_AGE_MIN
            now_ok, now_reason, now_metrics = strict_cross(m, now, total_swing_pct, min_step_pct)

            if not (fresh and now_ok):
                rec = {
                    'symbol': sym, 'time': pd.Timestamp(ts), 'cross_time': cross,
                    'age_min': age, 'cross_reason': cross_reason, 'now_reason': now_reason,
                }
                mm = now_metrics if now_metrics is not None else metrics
                if mm is not None:
                    rec.update({k:mm[k] for k in ['g0','g1','g2','g3','step1','step2','step3','total_swing']})
                blocked.append(rec)
                continue
            out.setdefault(pd.Timestamp(ts), []).append(c)
    return out, pd.DataFrame(blocked)


def net_stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n>0].sum()) if len(n) else 0.0
    gl = float(-n[n<0].sum()) if len(n) else 0.0
    return {
        'label': label, 'trades': len(n), 'net_wins': int((n>0).sum()),
        'net_win_pct': float((n>0).mean()*100) if len(n) else 0.0,
        'net_sum_pct': float(n.sum()) if len(n) else 0.0,
        'net_pf': gp/gl if gl>0 else np.inf,
        'gross_sum_pct': float(g.sum()) if len(g) else 0.0,
    }


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s:v10._refine_entry_frame(f) for s,f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17, _, _ = v17b.build_v17b(ev16, scored, waits)
    micros = {str(s).zfill(6):h.build_micro(b, cfg) for s,b in raw.items()}
    ev18, _ = h.build_veto_stream(ev17, micros)

    print('=== V20 STRICT 4-BAR MACD REAL CROSS SWEEP ===')
    print('BUY requires a completed pattern like -50 -> -20 -> +10 -> +30, normalized by price.')
    print('Weak rubbing/slight crosses are discarded. No future bar is used.')
    print('Fee: 0.25% round-trip. Ranking uses NET metrics.')

    rows=[]
    target_sym, target_ts = TARGET

    for total in TOTAL_SWING_LEVELS:
        for step in MIN_STEP_LEVELS:
            base_f, blocked = veto_base(ev18, micros, total, step)
            fast, diag = build_fast(scored, micros, raw, total, step)
            merged, _ = v19.merge_additive(base_f, fast)
            t = multi.simulate_multi(packed, merged, states, THRESHOLD)

            label=f'SWING{total:.3f}_STEP{step:.3f}'
            s=net_stats(label,t)
            s.update({
                'total_swing_pct': total,
                'min_step_pct': step,
                'base_blocked': len(blocked),
                'fast_triggered': int((diag.status=='TRIGGERED').sum()) if len(diag) else 0,
            })
            rows.append(s)

            hit=any(
                str(c[0]).zfill(6)==target_sym and pd.Timestamp(ts)==target_ts
                for ts,cs in merged.items() for c in cs
            )
            tb=blocked[(blocked.symbol==target_sym)&(blocked.time==target_ts)] if len(blocked) else pd.DataFrame()
            print(f'{label} TARGET_950260_0821_1000=', 'FAIL_PRESENT' if hit else 'PASS_BLOCKED')
            if len(tb):
                print(tb.to_string(index=False))

    summary=pd.DataFrame(rows).sort_values(
        ['net_sum_pct','net_pf','net_win_pct'], ascending=False
    )
    print('\n=== SUMMARY (NET 0.25%) ===')
    print(summary.to_string(index=False))
    print('\n=== BEST ===')
    print(summary.iloc[0].to_string())


if __name__=='__main__':
    main()
