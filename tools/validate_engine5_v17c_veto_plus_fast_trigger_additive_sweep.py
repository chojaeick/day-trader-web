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


def event_keys(events):
    return {
        (str(c[0]).zfill(6), pd.Timestamp(ts))
        for ts, rows in events.items()
        for c in rows
    }


def add_fast_events(veto_events, hybrid_events, ready_diag, max_delay):
    """Preserve every veto-surviving V17C event and add only fresh fast triggers.

    The additive stream is intentionally conservative:
    - trigger must come from the 5m-context/1m-trigger experiment;
    - READY->trigger delay must be <= max_delay;
    - exact events already present in the veto stream are not duplicated;
    - duplicate symbol+timestamp triggers are collapsed.

    No existing VETO event is removed or altered.
    """
    out = {pd.Timestamp(ts): list(rows) for ts, rows in veto_events.items()}
    existing = event_keys(out)
    added = []
    seen = set()

    ok = ready_diag[
        (ready_diag['status'] == 'TRIGGERED')
        & pd.to_numeric(ready_diag['delay_min'], errors='coerce').notna()
        & (pd.to_numeric(ready_diag['delay_min'], errors='coerce') <= float(max_delay))
    ].copy()

    allowed = {
        (str(r.symbol).zfill(6), pd.Timestamp(r.trigger_time))
        for r in ok.itertuples(index=False)
    }

    for ts in sorted(hybrid_events):
        for c in hybrid_events[ts]:
            sym = str(c[0]).zfill(6)
            key = (sym, pd.Timestamp(ts))
            if key not in allowed or key in existing or key in seen:
                continue
            seen.add(key)
            out.setdefault(pd.Timestamp(ts), []).append(c)
            recs = ok[(ok.symbol.astype(str).str.zfill(6) == sym) & (pd.to_datetime(ok.trigger_time) == pd.Timestamp(ts))]
            delay = float(pd.to_numeric(recs.iloc[0].delay_min, errors='coerce')) if len(recs) else np.nan
            ready_time = pd.Timestamp(recs.iloc[0].ready_time) if len(recs) else pd.NaT
            added.append({
                'symbol': sym,
                'ready_time': ready_time,
                'trigger_time': pd.Timestamp(ts),
                'trigger_price': float(c[1]),
                'score': float(c[2]),
                'delay_min': delay,
                'band_r_pct': float(c[5]) / float(c[1]) * 100.0 if float(c[1]) else np.nan,
            })

    return out, pd.DataFrame(added)


def realized_additive(base_t, candidate_t):
    bk = set(zip(base_t.symbol.astype(str).str.zfill(6), pd.to_datetime(base_t.entry_time)))
    x = candidate_t.copy()
    x['symbol'] = x.symbol.astype(str).str.zfill(6)
    x['entry_time'] = pd.to_datetime(x.entry_time)
    q = x[[ (s, t) not in bk for s, t in zip(x.symbol, x.entry_time) ]].copy()
    return q


def focus_report(label, events, micros):
    print(f'\n=== {label}: FOCUS WINDOWS ===')
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
    ev_veto, vetoed = h.build_veto_stream(ev_v17c, micros)
    ev_hybrid, ready_diag = h.build_ready_trigger_stream(scored, micros)

    t_v17c = multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)
    t_veto = multi.simulate_multi(packed, ev_veto, states, THRESHOLD)

    rows = [stats('V17C_BASE', t_v17c), stats('V17C_1M_VETO_BASE', t_veto)]
    cases = {}
    add_diags = {}

    for d in MAX_DELAYS:
        ev, add_diag = add_fast_events(ev_veto, ev_hybrid, ready_diag, d)
        t = multi.simulate_multi(packed, ev, states, THRESHOLD)
        label = f'VETO_PLUS_FAST_DELAY_LE_{d}M'
        rows.append(stats(label, t))
        cases[d] = (ev, t)
        add_diags[d] = add_diag

    summary = pd.DataFrame(rows)
    print('=== V17C 1M VETO BASE + ADDITIVE FAST TRIGGER SWEEP ===')
    print('V17C 1M VETO BASE is preserved unchanged.')
    print('Only 5m-context -> 1m positive MACD impulse + gap expansion + RSI rise triggers are ADDED.')
    print('Sweep varies maximum READY-to-trigger delay: 0/1/2/3 minutes.')
    print('No production rule is changed.')
    print('BREAKOUT_ADDED=', breakout_added)
    print('BREAKOUT_SKIPPED=', breakout_skipped)
    print('VETOED_RAW_EVENTS=', len(vetoed))
    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))

    print('\n=== ADDITIVE EVENT / REALIZED COUNTS ===')
    for d in MAX_DELAYS:
        ev, t = cases[d]
        rd = realized_additive(t_veto, t)
        ad = add_diags[d]
        print(f'DELAY<={d}m raw_added_events={len(ad)} realized_new_trades={len(rd)} realized_gross={rd.pnl_pct.sum() if len(rd) else 0.0:+.6f}%')

    best = summary.iloc[2:].sort_values(['gross_pct','pf'], ascending=False).iloc[0]
    best_delay = int(str(best['label']).split('_LE_')[1].replace('M',''))
    print('\n=== BEST BY GROSS ===')
    print(best.to_string())
    best_ev, best_t = cases[best_delay]
    focus_report(f'BEST DELAY<={best_delay}M', best_ev, micros)

    rd = realized_additive(t_veto, best_t)
    if len(rd):
        print('\n=== BEST: REALIZED NEW TRADES ===')
        cols = ['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','reason']
        print(rd[cols].sort_values('entry_time').to_string(index=False))
        print('\n=== BEST: WORST NEW TRADES ===')
        print(rd[cols].sort_values('pnl_pct').head(20).to_string(index=False))
        print('\n=== BEST: BEST NEW TRADES ===')
        print(rd[cols].sort_values('pnl_pct', ascending=False).head(20).to_string(index=False))

    for d in MAX_DELAYS:
        add_diags[d].to_csv(OUT_DIR / f'v17c_veto_fast_delay_le_{d}m_added_events.csv', index=False)
        cases[d][1].to_csv(OUT_DIR / f'v17c_veto_fast_delay_le_{d}m_trades.csv', index=False)
    summary.to_csv(OUT_DIR / 'v17c_veto_plus_fast_trigger_additive_sweep_summary.csv', index=False)
    print('\n[CSV]', OUT_DIR / 'v17c_veto_plus_fast_trigger_additive_sweep_summary.csv')


if __name__ == '__main__':
    main()
