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
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
MAX_DELAYS = [0, 1, 2, 3]
FOCUS = [
    ('043260', pd.Timestamp('2026-08-18 10:25:00+09:00')),
    ('257720', pd.Timestamp('2026-08-18 14:30:00+09:00')),
    ('950160', pd.Timestamp('2026-08-21 09:50:00+09:00')),
]


def stats(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    gross = float(p.sum()) if len(p) else 0.0
    n = len(p)
    return {
        'label': label,
        'trades': n,
        'wins': int((p > 0).sum()),
        'losses': int((p <= 0).sum()),
        'win_pct': float((p > 0).mean() * 100.0) if n else 0.0,
        'gross_pct': gross,
        'avg_pct': float(p.mean()) if n else 0.0,
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


def build_v19_birth_events(scored, micros, raw, max_delay):
    """V19 additive path: one 1m trigger per fresh 5m momentum birth.

    Rules:
    - V18 remains untouched and is merged later.
    - A setup arms only on 5m context False -> True transition.
    - Context = valid DBB + close>mid + trend_up + MACD context + score>=50.
    - The symbol's first trading date in the dataset is ineligible for this fast path.
      This hard-blocks first-day/no-established-Bollinger cases such as 950260 on 2026-08-18.
    - After birth, first positive 1m MACD impulse + positive gap delta + positive RSI slope
      may fire within max_delay minutes.
    - Whether fired or expired, no new arm occurs until context first becomes False and then True again.
    - Completed bars only; no backdating/lookahead.
    """
    first_dates = first_trading_dates(raw)
    events = {}
    diag = []
    seen = set()

    for sym0, f in scored.items():
        sym = str(sym0).zfill(6)
        m = micros[sym]
        z = f.copy().sort_values('time').reset_index(drop=True)
        prev_context = False

        for _, row in z.iterrows():
            ts = pd.Timestamp(row['time'])
            minute = ts.hour * 60 + ts.minute

            iu = h.finite(row.get('inner_upper'))
            il = h.finite(row.get('inner_lower'))
            mid = h.finite(row.get('mid'))
            close5 = h.finite(row.get('close'))
            score = h.finite(row.get('entry_score'))
            bb_valid = np.isfinite(iu) and np.isfinite(il) and iu > il and np.isfinite(mid)
            context = bool(
                minute >= 9 * 60 + 10
                and minute < base.NO_ENTRY_MINUTE
                and bb_valid
                and np.isfinite(close5) and close5 > mid
                and bool(row.get('trend_up', False))
                and bool(row.get('gate_macd_context', False))
                and np.isfinite(score) and score >= THRESHOLD
            )

            birth = context and not prev_context
            prev_context = context
            if not birth:
                continue

            first_day = ts.date() == first_dates.get(sym)
            rec = {
                'symbol': sym,
                'birth_time': ts,
                'birth_close_5m': close5,
                'score': score,
                'first_day_block': bool(first_day),
                'trigger_time': pd.NaT,
                'trigger_price': np.nan,
                'delay_min': np.nan,
                'status': 'FIRST_DAY_BLOCK' if first_day else 'NO_1M_TRIGGER',
            }
            if first_day:
                diag.append(rec)
                continue

            q = m[(m['time'] >= ts) & (m['time'] <= ts + pd.Timedelta(minutes=max_delay))].copy()
            prev = h.micro_row_at(m, ts - pd.Timedelta(minutes=1))
            chosen = None
            for _, mr in q.iterrows():
                if h.fast_trigger(prev, mr):
                    chosen = mr
                    break
                prev = mr

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
    existing = {
        (str(c[0]).zfill(6), pd.Timestamp(ts))
        for ts, rows in out.items() for c in rows
    }
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


def focus_report(events, micros):
    print('\n=== V19 FOCUS WINDOWS ===')
    for sym, target in FOCUS:
        rows = []
        for ts in sorted(events):
            if target - pd.Timedelta(minutes=20) <= pd.Timestamp(ts) <= target + pd.Timedelta(minutes=3):
                for c in events[ts]:
                    if str(c[0]).zfill(6) != sym:
                        continue
                    mr = h.micro_row_at(micros[sym], pd.Timestamp(ts))
                    rows.append({
                        'time': pd.Timestamp(ts),
                        'price': float(c[1]),
                        'score': float(c[2]),
                        'gap_delta': h.finite(mr['macd_gap_delta_1m']) if mr is not None else np.nan,
                        'rsi_slope': h.finite(mr['rsi_slope_1m']) if mr is not None else np.nan,
                    })
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
    cases = {}
    diags = {}

    for d in MAX_DELAYS:
        fast, diag = build_v19_birth_events(scored, micros, raw, d)
        merged, raw_added = merge_additive(ev_v18, fast)
        t = multi.simulate_multi(packed, merged, states, THRESHOLD)
        label = f'V19_DELAY_LE_{d}M'
        rows.append(stats(label, t))
        cases[d] = (merged, t, raw_added)
        diags[d] = diag

    summary = pd.DataFrame(rows)
    print('=== V19 VALIDATION ===')
    print('V17C = original 5m engine.')
    print('V18 = V17C + 1m stale-entry veto. Frozen base for this test.')
    print('V19 = V18 + one-shot fast trigger only on fresh 5m context False->True momentum birth.')
    print('First trading day is hard-blocked from V19 fast-path entries.')
    print('No production rule is changed.')
    print('BREAKOUT_ADDED=', breakout_added)
    print('BREAKOUT_SKIPPED=', breakout_skipped)
    print('V18_VETOED_RAW_EVENTS=', len(vetoed))
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== V19 ADDITIVE COUNTS ===')
    for d in MAX_DELAYS:
        merged, t, raw_added = cases[d]
        rd = realized_new_trades(t_v18, t)
        diag = diags[d]
        births = len(diag)
        firstday = int((diag.status == 'FIRST_DAY_BLOCK').sum()) if len(diag) else 0
        trig = int((diag.status == 'TRIGGERED').sum()) if len(diag) else 0
        print(
            f'DELAY<={d}m births={births} first_day_block={firstday} triggered={trig} '
            f'raw_added_events={len(raw_added)} realized_new_trades={len(rd)} '
            f'realized_gross={rd.pnl_pct.sum() if len(rd) else 0.0:+.6f}%'
        )

    best = summary.iloc[2:].sort_values(['gross_pct', 'pf'], ascending=False).iloc[0]
    best_delay = int(str(best['label']).split('_LE_')[1].replace('M', ''))
    print('\n=== BEST V19 BY GROSS ===')
    print(best.to_string())

    best_ev, best_t, _ = cases[best_delay]
    focus_report(best_ev, micros)

    rd = realized_new_trades(t_v18, best_t)
    cols = ['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','reason']
    print('\n=== BEST V19: REALIZED NEW TRADES ===')
    print(rd[cols].sort_values('entry_time').to_string(index=False) if len(rd) else 'NONE')
    print('\n=== BEST V19: WORST NEW TRADES ===')
    print(rd[cols].sort_values('pnl_pct').head(20).to_string(index=False) if len(rd) else 'NONE')
    print('\n=== BEST V19: BEST NEW TRADES ===')
    print(rd[cols].sort_values('pnl_pct', ascending=False).head(20).to_string(index=False) if len(rd) else 'NONE')

    print('\n=== FIRST-DAY BLOCK CHECK ===')
    bd = diags[best_delay]
    q = bd[bd.first_day_block].copy() if len(bd) else bd
    print(q[['symbol','birth_time','status']].sort_values(['birth_time','symbol']).to_string(index=False) if len(q) else 'NONE')

    for d in MAX_DELAYS:
        diags[d].to_csv(OUT_DIR / f'v19_delay_le_{d}m_birth_diag.csv', index=False)
        cases[d][1].to_csv(OUT_DIR / f'v19_delay_le_{d}m_trades.csv', index=False)
    summary.to_csv(OUT_DIR / 'v19_validation_summary.csv', index=False)
    print('\n[CSV]', OUT_DIR / 'v19_validation_summary.csv')


if __name__ == '__main__':
    main()
