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
LOOKBACK = 5
MAX_AGE_MIN = 2
TARGET = ('950260', pd.Timestamp('2026-08-21 10:00:00+09:00'))

# All quantities below are normalized by price so symbols remain comparable.
# flat_range_pct: max-min MACD/signal gap over the PREVIOUS 5 completed 1m bars.
# gap_level_pct: minimum actual MACD>signal separation before entry.
# power_mult: current gap-expansion impulse versus the recent 5-bar baseline.
FLAT_RANGE_LEVELS = [0.02, 0.03, 0.05]
GAP_LEVELS = [0.03, 0.05, 0.08, 0.10]
POWER_MULTS = [1.5, 2.0, 3.0]


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


def gap_delta_pct(row):
    if row is None:
        return np.nan
    return norm_pct(row.get('macd_gap_delta_1m'), row.get('close'))


def spread_pct(row):
    if row is None:
        return np.nan
    return norm_pct(row.get('spread_1m'), row.get('close'))


def prior5(m, ts):
    q = m[m.time < pd.Timestamp(ts)].tail(LOOKBACK).copy()
    return q if len(q) == LOOKBACK else pd.DataFrame()


def prior5_metrics(m, ts):
    q = prior5(m, ts)
    if q.empty:
        return None
    gps = np.array([gap_pct(r) for _, r in q.iterrows()], dtype=float)
    gds = np.array([gap_delta_pct(r) for _, r in q.iterrows()], dtype=float)
    sps = np.array([spread_pct(r) for _, r in q.iterrows()], dtype=float)
    if not np.all(np.isfinite(gps)):
        return None
    return {
        'gap_min': float(np.min(gps)),
        'gap_max': float(np.max(gps)),
        'gap_range': float(np.max(gps) - np.min(gps)),
        'gap_abs_mean': float(np.mean(np.abs(gps))),
        'gap_delta_abs_med': float(np.nanmedian(np.abs(gds))) if np.any(np.isfinite(gds)) else 0.0,
        'spread_abs_med': float(np.nanmedian(np.abs(sps))) if np.any(np.isfinite(sps)) else 0.0,
    }


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


def real_cross(m, row, flat_range_pct, gap_level_pct, power_mult):
    """Reject weak MACD/Signal rubbing; require a genuine separation impulse.

    If the previous five bars were grinding with nearly unchanged MACD-signal
    spacing, MACD>signal alone is only READY. Entry requires BOTH meaningful
    separation and a gap/spread impulse materially stronger than the recent
    five-bar baseline.
    """
    if row is None or not base_direction_ok(row):
        return False, 'BASE_DIRECTION_FAIL', None
    ts = pd.Timestamp(row.time)
    pm = prior5_metrics(m, ts)
    if pm is None:
        return False, 'NO_PRIOR5', None

    gp = gap_pct(row)
    gd = gap_delta_pct(row)
    sp = spread_pct(row)
    if not all(np.isfinite(x) for x in [gp, gd, sp]):
        return False, 'NONFINITE', pm

    # Not enough actual separation: never call this a real golden cross.
    if gp < gap_level_pct:
        return False, 'GAP_TOO_SMALL', pm

    flat = pm['gap_range'] <= flat_range_pct
    if not flat:
        return True, 'NORMAL_CROSS', pm

    # Previous five bars were rubbing/parallel. Demand a dramatic widening now.
    gd_base = max(pm['gap_delta_abs_med'], 0.002)
    sp_base = max(pm['spread_abs_med'], 0.002)
    power_ok = (gd >= power_mult * gd_base) and (sp >= power_mult * sp_base)
    if not power_ok:
        return False, 'FLAT5_WEAK_POWER', pm
    return True, 'FLAT5_REAL_CROSS', pm


def first_real_cross(m, end_ts, flat_range_pct, gap_level_pct, power_mult, lookback_min=12):
    q = m[(m.time >= pd.Timestamp(end_ts) - pd.Timedelta(minutes=lookback_min)) & (m.time <= pd.Timestamp(end_ts))]
    for _, r in q.iterrows():
        ok, reason, pm = real_cross(m, r, flat_range_pct, gap_level_pct, power_mult)
        if ok:
            return pd.Timestamp(r.time), reason
    return pd.NaT, None


def build_fast(scored, micros, raw, flat_range_pct, gap_level_pct, power_mult):
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

            old_cross, old_reason = first_real_cross(m, ts - pd.Timedelta(minutes=1), flat_range_pct, gap_level_pct, power_mult)
            if pd.notna(old_cross):
                diag.append({'symbol':sym,'ready_time':ts,'status':'STALE_PRIOR_REAL_CROSS','cross_time':old_cross,
                             'cross_reason':old_reason})
                continue

            q = m[(m.time >= ts) & (m.time <= ts + pd.Timedelta(minutes=MAX_AGE_MIN))]
            chosen = None; chosen_reason = None; chosen_pm = None
            for _, r in q.iterrows():
                ok, reason, pm = real_cross(m, r, flat_range_pct, gap_level_pct, power_mult)
                if ok:
                    chosen = r; chosen_reason = reason; chosen_pm = pm; break
            if chosen is None:
                diag.append({'symbol':sym,'ready_time':ts,'status':'NO_REAL_CROSS','cross_time':pd.NaT,
                             'cross_reason':None})
                continue

            dts = pd.Timestamp(chosen.time)
            ev = h.event_from_5m_row(sym, row5, dts, finite(chosen.close))
            key = (sym, dts)
            if ev is not None and key not in seen:
                seen.add(key)
                events.setdefault(dts, []).append(ev)
                diag.append({'symbol':sym,'ready_time':ts,'status':'TRIGGERED','cross_time':dts,
                             'cross_reason':chosen_reason,'prior5_gap_range':chosen_pm['gap_range'],
                             'gap_pct':gap_pct(chosen),'gap_delta_pct':gap_delta_pct(chosen),
                             'spread_pct':spread_pct(chosen)})
    return events, pd.DataFrame(diag)


def veto_base(ev18, micros, flat_range_pct, gap_level_pct, power_mult):
    out, blocked = {}, []
    for ts in sorted(ev18):
        for c in ev18[ts]:
            sym = str(c[0]).zfill(6)
            m = micros[sym]
            now = h.micro_row_at(m, ts)
            cross, cross_reason = first_real_cross(m, ts, flat_range_pct, gap_level_pct, power_mult)
            age = np.nan if pd.isna(cross) else (pd.Timestamp(ts)-cross).total_seconds()/60.0
            now_ok, now_reason, pm = real_cross(m, now, flat_range_pct, gap_level_pct, power_mult)
            fresh = pd.notna(cross) and 0 <= age <= MAX_AGE_MIN
            if not (fresh and now_ok):
                blocked.append({'symbol':sym,'time':pd.Timestamp(ts),'cross_time':cross,'age_min':age,
                                'cross_reason':cross_reason,'now_reason':now_reason,
                                'gap_pct':gap_pct(now),'prior5_gap_range':np.nan if pm is None else pm['gap_range']})
                continue
            out.setdefault(pd.Timestamp(ts), []).append(c)
    return out, pd.DataFrame(blocked)


def net_stats(label, t):
    g = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    n = g - FEE_RT_PCT
    gp = float(n[n>0].sum()) if len(n) else 0.0
    gl = float(-n[n<0].sum()) if len(n) else 0.0
    return {'label':label,'trades':len(n),'net_wins':int((n>0).sum()),
            'net_win_pct':float((n>0).mean()*100) if len(n) else 0.0,
            'net_sum_pct':float(n.sum()) if len(n) else 0.0,
            'net_pf':gp/gl if gl>0 else np.inf,'gross_sum_pct':float(g.sum()) if len(g) else 0.0}


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

    print('=== V20 REAL MACD CROSS POWER SWEEP ===')
    print('MACD>signal alone is READY, not BUY.')
    print('If previous 5x1m gaps are flat/grinding, entry requires a dramatic gap+spread power expansion.')
    print('Fee: 0.25% round-trip. Ranking uses NET metrics.')

    rows=[]
    target_sym, target_ts = TARGET
    for flat in FLAT_RANGE_LEVELS:
        for gap in GAP_LEVELS:
            for power in POWER_MULTS:
                base_f, blocked = veto_base(ev18, micros, flat, gap, power)
                fast, diag = build_fast(scored, micros, raw, flat, gap, power)
                merged, _ = v19.merge_additive(base_f, fast)
                t = multi.simulate_multi(packed, merged, states, THRESHOLD)
                label=f'FLAT{flat:.2f}_GAP{gap:.2f}_PWR{power:.1f}'
                s=net_stats(label,t)
                s.update({'flat_range_pct':flat,'gap_level_pct':gap,'power_mult':power,
                          'base_blocked':len(blocked),
                          'fast_triggered':int((diag.status=='TRIGGERED').sum()) if len(diag) else 0})
                rows.append(s)

                hit=any(str(c[0]).zfill(6)==target_sym and pd.Timestamp(ts)==target_ts for ts,cs in merged.items() for c in cs)
                tb=blocked[(blocked.symbol==target_sym)&(blocked.time==target_ts)] if len(blocked) else pd.DataFrame()
                print(f'{label} TARGET_950260_0821_1000=', 'FAIL_PRESENT' if hit else 'PASS_BLOCKED')
                if len(tb):
                    print(tb.to_string(index=False))

    summary=pd.DataFrame(rows).sort_values(['net_sum_pct','net_pf','net_win_pct'],ascending=False)
    print('\n=== SUMMARY TOP 15 (NET 0.25%) ===')
    print(summary.head(15).to_string(index=False))
    print('\n=== BEST ===')
    print(summary.iloc[0].to_string())


if __name__=='__main__':
    main()
