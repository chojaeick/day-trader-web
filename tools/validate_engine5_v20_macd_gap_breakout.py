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
# MACD-signal gap as % of current price. Absolute MACD values are not comparable across symbols.
GAP_PCT_LEVELS = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]
MAX_AGE_MIN = 2
TARGET = ('950260', pd.Timestamp('2026-08-21 10:00:00+09:00'))


def gap_pct(row):
    if row is None:
        return np.nan
    close = h.finite(row.get('close'))
    macd = h.finite(row.get('macd_1m'))
    sig = h.finite(row.get('signal_1m'))
    if not np.isfinite(close) or close <= 0 or not np.isfinite(macd) or not np.isfinite(sig):
        return np.nan
    return (macd - sig) / close * 100.0


def base_impulse_ok(row):
    if row is None:
        return False
    vals = [row.get('macd_1m'), row.get('signal_1m'), row.get('macd_slope_1m'),
            row.get('macd_gap_delta_1m'), row.get('rsi_slope_1m')]
    if not all(np.isfinite(h.finite(x)) for x in vals):
        return False
    return bool(
        h.finite(row['macd_1m']) > h.finite(row['signal_1m'])
        and h.finite(row['macd_slope_1m']) > 0
        and h.finite(row['macd_gap_delta_1m']) > 0
        and h.finite(row['rsi_slope_1m']) > 0
    )


def gap_breakout(prev, row, level):
    if not base_impulse_ok(row):
        return False
    gp = gap_pct(row)
    pg = gap_pct(prev)
    if not np.isfinite(gp):
        return False
    # Entry is on the FIRST crossing, not merely because the gap is already large.
    return bool(gp >= level and (not np.isfinite(pg) or pg < level))


def first_cross_time(m, ts, level, lookback_min=12):
    q = m[(m.time >= ts - pd.Timedelta(minutes=lookback_min)) & (m.time <= ts)].copy()
    prev = None
    for _, r in q.iterrows():
        if gap_breakout(prev, r, level):
            return pd.Timestamp(r.time)
        prev = r
    return pd.NaT


def build_fast(scored, micros, raw, level):
    first_dates = v19.first_trading_dates(raw)
    events, diag = {}, []
    seen = set()
    for sym0, f in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        z = f.copy().sort_values('time').reset_index(drop=True)
        prev_pre = False
        for _, row in z.iterrows():
            ts = pd.Timestamp(row.time)
            minute = ts.hour * 60 + ts.minute
            pre = bool(9 * 60 + 10 <= minute < base.NO_ENTRY_MINUTE and v19.prebuy_5m(row))
            birth = pre and not prev_pre
            prev_pre = pre
            if not birth:
                continue
            if ts.date() == first_dates.get(sym):
                continue

            # If this MACD-gap breakout already happened before READY, this wave is stale.
            crossed_before = first_cross_time(m, ts, level)
            if pd.notna(crossed_before) and crossed_before < ts:
                age = (ts - crossed_before).total_seconds() / 60.0
                diag.append({'symbol':sym,'ready_time':ts,'level':level,'status':'STALE_PRIOR_CROSS',
                             'cross_time':crossed_before,'age_min':age})
                continue

            q = m[(m.time >= ts) & (m.time <= ts + pd.Timedelta(minutes=MAX_AGE_MIN))].copy()
            prev = h.micro_row_at(m, ts - pd.Timedelta(minutes=1))
            chosen = None
            for _, mr in q.iterrows():
                if gap_breakout(prev, mr, level):
                    chosen = mr
                    break
                prev = mr
            if chosen is None:
                diag.append({'symbol':sym,'ready_time':ts,'level':level,'status':'NO_GAP_BREAKOUT',
                             'cross_time':pd.NaT,'age_min':np.nan})
                continue
            dts = pd.Timestamp(chosen.time)
            ev = h.event_from_5m_row(sym, row, dts, h.finite(chosen.close))
            key = (sym, dts)
            if ev is not None and key not in seen:
                seen.add(key)
                events.setdefault(dts, []).append(ev)
                diag.append({'symbol':sym,'ready_time':ts,'level':level,'status':'TRIGGERED',
                             'cross_time':dts,'age_min':(dts-ts).total_seconds()/60.0})
    return events, pd.DataFrame(diag)


def veto_late_base(ev_v18, micros, level):
    out, blocked = {}, []
    for ts in sorted(ev_v18):
        for c in ev_v18[ts]:
            sym = str(c[0]).zfill(6)
            m = micros[sym]
            now = h.micro_row_at(m, ts)
            cross = first_cross_time(m, pd.Timestamp(ts), level)
            # Base entry is valid only if the threshold breakout is fresh (0..MAX_AGE_MIN minutes old)
            # and current impulse has not already faded.
            age = np.nan if pd.isna(cross) else (pd.Timestamp(ts)-cross).total_seconds()/60.0
            fresh = pd.notna(cross) and 0 <= age <= MAX_AGE_MIN
            current_ok = base_impulse_ok(now)
            if not (fresh and current_ok):
                blocked.append({'symbol':sym,'time':pd.Timestamp(ts),'level':level,'cross_time':cross,
                                'age_min':age,'current_ok':current_ok,'gap_pct':gap_pct(now)})
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

    rows=[]
    target_sym,target_ts=TARGET
    print('=== V20 MACD-SIGNAL GAP BREAKOUT SWEEP ===')
    print('Rule: MACD>signal is READY only. Entry requires FIRST gap_pct threshold crossing.')
    print(f'Base V18 late entries are also vetoed if the crossing is older than {MAX_AGE_MIN}m or current impulse has faded.')
    print('Fee: 0.25% round trip; ranking uses NET metrics.')
    for level in GAP_PCT_LEVELS:
        base_filtered, blocked = veto_late_base(ev18, micros, level)
        fast, diag = build_fast(scored, micros, raw, level)
        merged, _ = v19.merge_additive(base_filtered, fast)
        t = multi.simulate_multi(packed, merged, states, THRESHOLD)
        s = net_stats(f'GAP_{level:.2f}PCT', t)
        s['base_blocked']=len(blocked); s['fast_triggered']=int((diag.status=='TRIGGERED').sum()) if len(diag) else 0
        rows.append(s)

        hit = any(str(c[0]).zfill(6)==target_sym and pd.Timestamp(ts)==target_ts for ts,cs in merged.items() for c in cs)
        tb = blocked[(blocked.symbol==target_sym) & (blocked.time==target_ts)] if len(blocked) else pd.DataFrame()
        print(f'LEVEL={level:.2f}% TARGET_950260_0821_1000=', 'FAIL_PRESENT' if hit else 'PASS_BLOCKED')
        if len(tb): print(tb.to_string(index=False))

    summary=pd.DataFrame(rows).sort_values(['net_sum_pct','net_pf','net_win_pct'],ascending=False)
    print('\n=== SUMMARY (NET 0.25%) ===')
    print(summary.to_string(index=False))
    print('\n=== BEST ===')
    print(summary.iloc[0].to_string())

if __name__=='__main__':
    main()
