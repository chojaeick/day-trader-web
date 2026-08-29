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
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
import tools.validate_engine5_v18_outer_upper_breakout_filter as v18
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
RESISTANCE_RATIO = 0.985
EDGE_MIN = 0.10
TARGET_SYM = '950260'
TARGET_TS = pd.Timestamp('2026-08-21 10:00:00+09:00')


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def resistance_diag(event, ts, frames):
    d = v18.event_diag(event, ts, frames)
    ou = float(d['outer_upper']) if np.isfinite(d['outer_upper']) else np.nan
    close = float(d['close'])
    proximity = close / ou if np.isfinite(ou) and ou > 0 else np.nan
    d['outer_proximity'] = proximity
    d['in_resistance_zone'] = bool(np.isfinite(proximity) and proximity >= RESISTANCE_RATIO)
    d['weak_edge'] = bool(np.isfinite(d['ratio_edge']) and d['ratio_edge'] <= EDGE_MIN)
    return d


def filter_resistance_zone(events, frames):
    """Keep V17C/V17B semantics and alter only ordinary entries:

    - Existing massive-volume breakout entries are preserved untouched.
    - Ordinary entries outside the outer-upper resistance zone are untouched.
    - In the resistance zone (close >= 98.5% of outer upper), block only when
      MACD_ratio - Signal_ratio <= 0.10.

    This is an isolated structural test, not a parameter sweep.
    """
    out = {}
    removed = []
    for ts, rows in events.items():
        keep = []
        for e in rows:
            d = resistance_diag(e, ts, frames)
            if d['breakout_entry']:
                keep.append(e)
                continue
            if d['in_resistance_zone'] and d['weak_edge']:
                d['filter_reason'] = 'OUTER_RESISTANCE_WEAK_MACD_SIGNAL_EDGE'
                removed.append(d)
            else:
                keep.append(e)
        if keep:
            out[ts] = keep
    return out, pd.DataFrame(removed)


def metrics(name, trades):
    if trades is None or len(trades) == 0:
        return {'name': name, 'trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0.0, 'gross_pct': 0.0, 'avg_pct': 0.0, 'pf': np.nan, 'max_loss_pct': np.nan}
    p = pd.to_numeric(trades['pnl_pct'], errors='coerce').dropna()
    wins = int((p > 0).sum())
    losses = int((p <= 0).sum())
    gp = float(p[p > 0].sum())
    gl = float(-p[p < 0].sum())
    return {
        'name': name,
        'trades': int(len(p)),
        'wins': wins,
        'losses': losses,
        'win_rate': wins / len(p) * 100.0 if len(p) else 0.0,
        'gross_pct': float(p.sum()),
        'avg_pct': float(p.mean()),
        'pf': gp / gl if gl > 0 else np.inf,
        'max_loss_pct': float(p.min()) if len(p) else np.nan,
    }


def independent_removed_outcomes(removed, original_events, packed_exits, state_events):
    rows = []
    if removed is None or removed.empty:
        return pd.DataFrame()
    for d in removed.itertuples(index=False):
        ts = pd.Timestamp(d.time)
        sym = str(d.symbol).zfill(6)
        es = [e for e in original_events.get(ts, []) if str(e[0]).zfill(6) == sym]
        if not es:
            continue
        t = v17c.simulate_unconditional_hwm(packed_exits, {ts: [es[0]]}, state_events, THRESHOLD)
        rec = dict(d._asdict())
        if len(t):
            r = t.iloc[0]
            rec.update({
                'ind_exit_time': pd.Timestamp(r.exit_time),
                'ind_pnl_pct': float(r.pnl_pct),
                'ind_reason': str(r.reason),
            })
        else:
            rec.update({'ind_exit_time': pd.NaT, 'ind_pnl_pct': np.nan, 'ind_reason': 'NO_TRADE'})
        rows.append(rec)
    return pd.DataFrame(rows)


def key_present(events, sym, ts):
    t = pd.Timestamp(ts)
    return any(str(e[0]).zfill(6) == str(sym).zfill(6) for e in events.get(t, []))


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    diag_frames = v18.build_diag_frames(scored)

    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17b, added, skipped = v17b.build_v17b(ev16, scored, waits)
    ev18b, removed = filter_resistance_zone(ev17b, diag_frames)

    print('=== ENGINE5 V18B OUTER-UPPER RESISTANCE-ZONE WEAK-SLOPE FILTER ===')
    print('Single structural test only; no threshold sweep.')
    print(f'RESISTANCE ZONE: close / outer_upper >= {RESISTANCE_RATIO:.3f}')
    print(f'WEAK EDGE: MACD_ratio - Signal_ratio <= {EDGE_MIN:.2f}')
    print('ACTION: ordinary BUY in resistance zone + weak edge is blocked.')
    print('V17B massive-volume breakout entries are preserved untouched.')

    target_rows = []
    for ts, rows in ev17b.items():
        for e in rows:
            if str(e[0]).zfill(6) == TARGET_SYM and pd.Timestamp(ts) == TARGET_TS:
                target_rows.append(resistance_diag(e, ts, diag_frames))
    print('\n=== TARGET 950260 2026-08-21 10:00 ===')
    cols = ['symbol','time','close','outer_upper','outer_proximity','macd_ratio','signal_ratio','ratio_edge','rsi_slope','prev_rsi_slope','volume_prev_ratio','in_resistance_zone','weak_edge','breakout_entry']
    td = pd.DataFrame(target_rows)
    print(td[[c for c in cols if c in td.columns]].to_string(index=False) if len(td) else 'TARGET_EVENT_NOT_FOUND')

    ta = v17c.simulate_unconditional_hwm(packed_exits, ev17b, state_events, THRESHOLD)
    tb = v17c.simulate_unconditional_hwm(packed_exits, ev18b, state_events, THRESHOLD)
    ma = metrics('A_V17C_CURRENT', ta)
    mb = metrics('B_V18B_RESISTANCE_WEAK_EDGE', tb)
    ma['removed_candidates'] = 0
    mb['removed_candidates'] = int(len(removed))

    print('\n=== FULL PATH COMPARISON ===')
    for m in [ma, mb]:
        print(f"{m['name']}: trades={m['trades']} wins={m['wins']} losses={m['losses']} win={m['win_rate']:.2f}% gross={m['gross_pct']:+.4f}% avg={m['avg_pct']:+.4f}% pf={m['pf']:.3f} maxloss={m['max_loss_pct']:+.4f}% removed={m['removed_candidates']}")
    print(f"DELTA: trades={mb['trades']-ma['trades']:+d} win={mb['win_rate']-ma['win_rate']:+.2f}pp gross={mb['gross_pct']-ma['gross_pct']:+.4f}%p avg={mb['avg_pct']-ma['avg_pct']:+.4f}%p pf={mb['pf']-ma['pf']:+.3f}")

    print('\n=== REMOVED CANDIDATES + INDEPENDENT CURRENT-RULE OUTCOME ===')
    ind = independent_removed_outcomes(removed, ev17b, packed_exits, state_events)
    if ind.empty:
        print('none')
    else:
        outcols = ['symbol','time','close','outer_upper','outer_proximity','macd_ratio','signal_ratio','ratio_edge','rsi_slope','prev_rsi_slope','volume_prev_ratio','ind_pnl_pct','ind_reason']
        print(ind[[c for c in outcols if c in ind.columns]].to_string(index=False))
        p = pd.to_numeric(ind['ind_pnl_pct'], errors='coerce').dropna()
        if len(p):
            print(f"REMOVED_OUTCOME_SUMMARY: n={len(p)} wins={(p>0).sum()} losses={(p<=0).sum()} win_rate={(p>0).mean()*100:.2f}% gross={p.sum():+.4f}% avg={p.mean():+.4f}%")
        ind.to_csv(OUTDIR / 'v18b_removed_independent.csv', index=False)

    target_removed = False if removed.empty else bool(((removed['symbol'].astype(str).str.zfill(6) == TARGET_SYM) & (pd.to_datetime(removed['time']) == TARGET_TS)).any())
    print('\n=== REGRESSION CHECKS ===')
    print('target950260_removed=', target_removed)
    print('preserve_257720_0812_0910=', key_present(ev18b, '257720', '2026-08-12 09:10:00+09:00'))
    print('preserve_257720_breakout_0818_1420=', key_present(ev18b, '257720', '2026-08-18 14:20:00+09:00'))
    print('preserve_484810_veto=', not key_present(ev18b, '484810', '2026-08-11 09:10:00+09:00'))

    board = pd.DataFrame([ma, mb])
    out = OUTDIR / 'v18b_outer_upper_resistance_zone_summary.csv'
    board.to_csv(out, index=False)
    print('\n[CSV]', out)


if __name__ == '__main__':
    main()
