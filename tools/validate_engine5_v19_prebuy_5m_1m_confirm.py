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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
MAX_DELAYS = [0, 1, 2, 3]
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
BAD_CASE = ('950260', pd.Timestamp('2026-08-19 09:30:00+09:00'))
FOCUS = [
    ('043260', pd.Timestamp('2026-08-18 10:25:00+09:00')),
    ('257720', pd.Timestamp('2026-08-18 14:30:00+09:00')),
    ('950160', pd.Timestamp('2026-08-21 09:50:00+09:00')),
    BAD_CASE,
]


def stats(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    gross = float(p.sum()) if len(p) else 0.0
    n = len(p)
    return {
        'label': label, 'trades': n,
        'wins': int((p > 0).sum()), 'losses': int((p <= 0).sum()),
        'win_pct': float((p > 0).mean() * 100.0) if n else 0.0,
        'gross_pct': gross, 'avg_pct': float(p.mean()) if n else 0.0,
        'pf': gp / gl if gl > 0 else np.inf,
        'maxloss_pct': float(p.min()) if n else np.nan,
        'net_rt025_sum_pct': gross - n * 0.25,
        'net_rt050_sum_pct': gross - n * 0.50,
    }


def first_trading_dates(raw):
    out = {}
    for sym, bars in raw.items():
        t = pd.to_datetime(bars['time'])
        out[str(sym).zfill(6)] = t.dt.date.min()
    return out


def prebuy_5m(row):
    """Strict 5m continuation pre-BUY.

    Preserve the V10 continuation structure and remove only the final MACD/RSI
    re-acceleration requirement. 1m is allowed to confirm those final momentum
    legs; it is never allowed to invent a candidate on its own.
    """
    iu = h.finite(row.get('inner_upper'))
    il = h.finite(row.get('inner_lower'))
    mid = h.finite(row.get('mid'))
    close5 = h.finite(row.get('close'))
    score = h.finite(row.get('entry_score'))
    bb_valid = np.isfinite(iu) and np.isfinite(il) and iu > il and np.isfinite(mid)
    structural = bool(
        bb_valid
        and np.isfinite(close5) and close5 > mid
        and bool(row.get('trend_up', False))
        and bool(row.get('gate_macd_context', False))
        and bool(row.get('gate_macd_rising', False))
        and bool(row.get('gate_rsi_persistent', False))
        and np.isfinite(score) and score >= THRESHOLD
    )
    # If full V10 BUY is already complete, V18 owns that entry. Fast path only
    # handles the strict pre-BUY state before final re-acceleration confirmation.
    return structural and not bool(row.get('entry_gate', False))


def final_1m_confirm(row):
    """Final execution approval only; no candidate generation here."""
    if row is None:
        return False
    vals = [
        row.get('macd_1m'), row.get('signal_1m'), row.get('macd_slope_1m'),
        row.get('macd_gap_delta_1m'), row.get('rsi_slope_1m')
    ]
    if not all(np.isfinite(h.finite(x)) for x in vals):
        return False
    return bool(
        h.finite(row['macd_1m']) > h.finite(row['signal_1m'])
        and h.finite(row['macd_slope_1m']) > 0
        and h.finite(row['macd_gap_delta_1m']) > 0
        and h.finite(row['rsi_slope_1m']) > 0
    )


def build_v19_events(scored, micros, raw, max_delay):
    first_dates = first_trading_dates(raw)
    events, diag = {}, []
    seen = set()

    for sym0, f in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        z = f.copy().sort_values('time').reset_index(drop=True)
        prev_prebuy = False

        for _, row in z.iterrows():
            ts = pd.Timestamp(row['time'])
            minute = ts.hour * 60 + ts.minute
            active = 9 * 60 + 10 <= minute < base.NO_ENTRY_MINUTE
            pre = bool(active and prebuy_5m(row))
            birth = pre and not prev_prebuy
            prev_prebuy = pre
            if not birth:
                continue

            first_day = ts.date() == first_dates.get(sym)
            rec = {
                'symbol': sym, 'ready_time': ts,
                'score': h.finite(row.get('entry_score')),
                'close5': h.finite(row.get('close')),
                'trend_up': bool(row.get('trend_up', False)),
                'gate_macd_context': bool(row.get('gate_macd_context', False)),
                'gate_macd_rising': bool(row.get('gate_macd_rising', False)),
                'gate_rsi_persistent': bool(row.get('gate_rsi_persistent', False)),
                'macd_spread_5m': h.finite(row.get('macd_slope_spread')),
                'macd_gap_delta_5m': h.finite(row.get('macd_gap_delta')),
                'rsi_slope_5m': h.finite(row.get('rsi_slope')),
                'first_day_block': bool(first_day),
                'trigger_time': pd.NaT, 'trigger_price': np.nan,
                'delay_min': np.nan,
                'status': 'FIRST_DAY_BLOCK' if first_day else 'NO_1M_CONFIRM',
            }
            if first_day:
                diag.append(rec)
                continue

            q = m[(m['time'] >= ts) & (m['time'] <= ts + pd.Timedelta(minutes=max_delay))].copy()
            chosen = None
            for _, mr in q.iterrows():
                if final_1m_confirm(mr):
                    chosen = mr
                    break

            if chosen is not None:
                dts = pd.Timestamp(chosen['time'])
                key = (sym, dts)
                ev = h.event_from_5m_row(sym, row, dts, h.finite(chosen['close']))
                if ev is not None and key not in seen:
                    seen.add(key)
                    events.setdefault(dts, []).append(ev)
                    rec.update({
                        'trigger_time': dts,
                        'trigger_price': h.finite(chosen['close']),
                        'delay_min': (dts - ts).total_seconds() / 60.0,
                        'status': 'TRIGGERED',
                    })
            diag.append(rec)

    return events, pd.DataFrame(diag)


def merge_additive(v18_events, fast_events):
    out = {pd.Timestamp(ts): list(rows) for ts, rows in v18_events.items()}
    existing = {(str(c[0]).zfill(6), pd.Timestamp(ts)) for ts, rows in out.items() for c in rows}
    added = []
    for ts in sorted(fast_events):
        for c in fast_events[ts]:
            key = (str(c[0]).zfill(6), pd.Timestamp(ts))
            if key in existing:
                continue
            out.setdefault(pd.Timestamp(ts), []).append(c)
            existing.add(key)
            added.append(key)
    return out, added


def realized_new_trades(base_t, candidate_t):
    bk = set(zip(base_t.symbol.astype(str).str.zfill(6), pd.to_datetime(base_t.entry_time)))
    x = candidate_t.copy()
    x['symbol'] = x.symbol.astype(str).str.zfill(6)
    x['entry_time'] = pd.to_datetime(x.entry_time)
    return x[[(s, t) not in bk for s, t in zip(x.symbol, x.entry_time)]].copy()


def five_min_diagnostic(scored, sym, start, end):
    f = scored[sym].copy()
    f['time'] = pd.to_datetime(f['time'])
    q = f[(f.time >= start) & (f.time <= end)].copy()
    rows = []
    for _, r in q.iterrows():
        rows.append({
            'time': r['time'], 'close': h.finite(r.get('close')), 'score': h.finite(r.get('entry_score')),
            'trend_up': bool(r.get('trend_up', False)),
            'macd_ctx': bool(r.get('gate_macd_context', False)),
            'macd_rising': bool(r.get('gate_macd_rising', False)),
            'rsi_persist': bool(r.get('gate_rsi_persistent', False)),
            'macd_spread': h.finite(r.get('macd_slope_spread')),
            'gap_delta': h.finite(r.get('macd_gap_delta')),
            'rsi_slope': h.finite(r.get('rsi_slope')),
            'full_entry_gate': bool(r.get('entry_gate', False)),
            'V19_PREBUY': prebuy_5m(r),
        })
    return pd.DataFrame(rows)


def focus_report(events):
    print('\n=== V19 FOCUS WINDOWS ===')
    for sym, target in FOCUS:
        rows = []
        for ts in sorted(events):
            if target - pd.Timedelta(minutes=20) <= ts <= target + pd.Timedelta(minutes=3):
                for c in events[ts]:
                    if str(c[0]).zfill(6) == sym:
                        rows.append({'time': ts, 'price': float(c[1]), 'score': float(c[2])})
        print(f'-- {sym} around {target} --')
        print(pd.DataFrame(rows).to_string(index=False) if rows else 'NONE')


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)

    raw_entries = v8.pack_entry_events(scored)
    ev10 = sweep.filt_open(raw_entries)
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, breakout_added, breakout_skipped = v17b.build_v17b(ev16, scored, waits)

    micros = {str(sym).zfill(6): h.build_micro(bars, cfg) for sym, bars in raw.items()}
    ev_v18, vetoed = h.build_veto_stream(ev_v17c, micros)
    t_v17c = multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)
    t_v18 = multi.simulate_multi(packed, ev_v18, states, THRESHOLD)

    rows = [stats('V17C', t_v17c), stats('V18', t_v18)]
    cases, diags = {}, {}
    for d in MAX_DELAYS:
        fast, diag = build_v19_events(scored, micros, raw, d)
        merged, raw_added = merge_additive(ev_v18, fast)
        t = multi.simulate_multi(packed, merged, states, THRESHOLD)
        label = f'V19_STRICT_PREBUY_DELAY_LE_{d}M'
        rows.append(stats(label, t))
        cases[d] = (merged, t, raw_added, fast)
        diags[d] = diag

    summary = pd.DataFrame(rows)
    print('=== V19 STRICT 5M PRE-BUY + 1M FINAL CONFIRM ===')
    print('V18 is frozen.')
    print('5m READY requires: valid BB + close>mid + trend_up + MACD context + MACD rising + RSI persistent + score>=50.')
    print('Full V10 entry_gate rows are NOT fast-path candidates; V18 owns them.')
    print('1m only approves execution: MACD>signal + MACD rising + gap expanding + RSI rising.')
    print('First trading day remains blocked from fast-path.')
    print('No production rule is changed.')
    print('BREAKOUT_ADDED=', breakout_added)
    print('BREAKOUT_SKIPPED=', breakout_skipped)
    print('V18_VETOED_RAW_EVENTS=', len(vetoed))
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== ADDITIVE COUNTS ===')
    for d in MAX_DELAYS:
        merged, t, raw_added, fast = cases[d]
        rd = realized_new_trades(t_v18, t)
        dg = diags[d]
        print(
            f'DELAY<={d}m ready={len(dg)} first_day_block={int((dg.status == "FIRST_DAY_BLOCK").sum()) if len(dg) else 0} '
            f'triggered={int((dg.status == "TRIGGERED").sum()) if len(dg) else 0} raw_added={len(raw_added)} '
            f'realized_new={len(rd)} realized_gross={rd.pnl_pct.sum() if len(rd) else 0.0:+.6f}%'
        )

    print('\n=== 950260 2026-08-19 5M DIAGNOSTIC ===')
    bad5 = five_min_diagnostic(
        scored, '950260',
        pd.Timestamp('2026-08-19 09:10:00+09:00'),
        pd.Timestamp('2026-08-19 09:40:00+09:00')
    )
    print(bad5.to_string(index=False) if len(bad5) else 'NONE')

    bad_sym, bad_ts = BAD_CASE
    print('\n=== HARD REGRESSION CHECK: 950260 2026-08-19 09:30 ===')
    all_pass = True
    for d in MAX_DELAYS:
        fast = cases[d][3]
        hit = any(
            str(c[0]).zfill(6) == bad_sym and pd.Timestamp(ts) == bad_ts
            for ts, cs in fast.items() for c in cs
        )
        all_pass = all_pass and not hit
        print(f'DELAY<={d}m:', 'FAIL_ENTRY_PRESENT' if hit else 'PASS_BLOCKED')
    print('REGRESSION_RESULT=', 'PASS' if all_pass else 'FAIL')

    best = summary.iloc[2:].sort_values(['gross_pct', 'pf'], ascending=False).iloc[0]
    best_delay = int(str(best['label']).split('_LE_')[1].replace('M', ''))
    print('\n=== BEST V19 BY GROSS ===')
    print(best.to_string())
    best_ev, best_t, _, _ = cases[best_delay]
    focus_report(best_ev)

    rd = realized_new_trades(t_v18, best_t)
    cols = ['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','r_pct','reason']
    cols = [c for c in cols if c in rd.columns]
    print('\n=== WORST NEW TRADES ===')
    print(rd[cols].sort_values('pnl_pct').head(20).to_string(index=False) if len(rd) else 'NONE')
    print('\n=== BEST NEW TRADES ===')
    print(rd[cols].sort_values('pnl_pct', ascending=False).head(20).to_string(index=False) if len(rd) else 'NONE')

    summary.to_csv(OUT_DIR / 'v19_strict_prebuy_summary.csv', index=False)
    bad5.to_csv(OUT_DIR / 'v19_950260_20260819_5m_diag.csv', index=False)
    for d in MAX_DELAYS:
        diags[d].to_csv(OUT_DIR / f'v19_strict_prebuy_delay_{d}m_diag.csv', index=False)
        cases[d][1].to_csv(OUT_DIR / f'v19_strict_prebuy_delay_{d}m_trades.csv', index=False)
    print('\n[CSV]', OUT_DIR / 'v19_strict_prebuy_summary.csv')


if __name__ == '__main__':
    main()
