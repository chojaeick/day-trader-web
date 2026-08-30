from __future__ import annotations

"""Audit whether V21E Slow-turn bypasses the normal entry-score gate.

Reads the already-built fresh SQLite/USD/ET map only; no DB remap and no retuning.
Compares each Slow-turn event's stored simulator score against the underlying scored-frame
entry_score at the causal 5m bar, then re-simulates Slow-turn with the artificial score
floor removed.
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_us_e_all_versions as e
import tools.remap_and_validate_engine5_v21e_fresh_from_us_db as fresh

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP_PKL = ROOT / 'v21e_fresh_map.pkl'
OUT = ROOT / 'v21e_slow_turn_score_floor_audit.csv'
TARGET_SYMBOL = '00SOXL'
TARGET_TIME = pd.Timestamp('2026-07-02 09:51:00', tz='America/New_York')


def n(x):
    return str(x).zfill(6)


def finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def lookup_scored_row(scored: dict[str, pd.DataFrame], sym: str, ts: pd.Timestamp):
    f = scored[n(sym)].copy()
    f['time'] = pd.to_datetime(f['time'])
    # Slow-turn builds its event from the latest completed 5m state at/before READY.
    # Entry happens on the subsequent 1m confirmation, so use the latest scored row
    # not later than entry.floor(5m).
    q = f[f.time <= pd.Timestamp(ts).floor('5min')]
    return None if q.empty else q.iloc[-1]


def replace_event_score(tag, score):
    x = dict(tag)
    ev = list(x['event'])
    ev[2] = float(score)
    x['event'] = tuple(ev)
    return x


def stat_line(label, trades):
    m = fresh.metrics(trades)
    return (
        f"{label}: trades={m['trades']} "
        f"netWR={m['net025_win_pct']:.2f}% net={m['net025_sum_pct']:+.4f}% "
        f"PF={m['net025_pf']:.3f} maxloss={m['max_net025_loss_pct']:+.4f}%"
    )


def main():
    if not MAP_PKL.exists():
        raise FileNotFoundError(MAP_PKL)

    with MAP_PKL.open('rb') as fh:
        d = pickle.load(fh)

    if d.get('schema') != 'V21E_FRESH_SQLITE_USD_ET_V1':
        raise RuntimeError(f"unexpected schema: {d.get('schema')}")

    e.apply_us_session_clock()
    raw = d['raw']
    scored = d['scored']
    tags = d['tags']
    slow = [x for x in tags if x['source'] == 'SLOW_TURN_E']

    # Rebuild packed/state streams exactly as the saved-map combination validator does.
    from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
    import tools.backtest_dbb_engine5_fast_tuner_v4 as base
    import tools.backtest_dbb_engine5_fast_tuner_v8 as v8

    cfg0 = DoubleBollingerEngine5Config()
    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))

    rows = []
    no_floor_tags = []
    for t in slow:
        ts = pd.Timestamp(t['time'])
        sym = n(t['symbol'])
        r = lookup_scored_row(scored, sym, ts)
        underlying = finite(r.get('entry_score')) if r is not None else np.nan
        event_score = finite(t['event'][2])
        macd_strength = finite(r.get('macd_slope_spread_strength')) if r is not None else np.nan
        rsi_strength = finite(r.get('rsi_slope_strength')) if r is not None else np.nan
        gap = finite(r.get('macd_gap')) if r is not None else np.nan
        gap_delta = finite(r.get('gap_delta')) if r is not None else np.nan
        rsi = finite(r.get('rsi')) if r is not None else np.nan
        rsi_slope = finite(r.get('rsi_slope')) if r is not None else np.nan
        trend_up = bool(r.get('trend_up')) if r is not None and pd.notna(r.get('trend_up')) else False

        rows.append(dict(
            symbol=sym,
            entry_time=ts,
            event_score=event_score,
            underlying_entry_score=underlying,
            forced_to_threshold=bool(np.isfinite(underlying) and event_score > underlying + 1e-12),
            underlying_pass50=bool(np.isfinite(underlying) and underlying >= 50.0),
            macd_slope_spread_strength=macd_strength,
            rsi_slope_strength=rsi_strength,
            macd_gap=gap,
            gap_delta=gap_delta,
            rsi=rsi,
            rsi_slope=rsi_slope,
            trend_up=trend_up,
            regime=(t.get('meta') or {}).get('regime', ''),
            norm_mid_slope_pct=(t.get('meta') or {}).get('norm_mid_slope_pct', np.nan),
        ))

        if np.isfinite(underlying):
            no_floor_tags.append(replace_event_score(t, underlying))

    out = pd.DataFrame(rows).sort_values(['entry_time','symbol']).reset_index(drop=True)
    out.to_csv(OUT, index=False)

    original_trades = integ.simulate(packed, states, slow)
    no_floor_trades = integ.simulate(packed, states, no_floor_tags)

    print('=== V21E SLOW-TURN SCORE-FLOOR AUDIT ===')
    print('Fresh SQLite/USD/ET map only. No DB remap. No threshold retuning.')
    print(f'signals={len(out)}')
    if len(out):
        print(f"forced_to_50={int(out.forced_to_threshold.sum())}/{len(out)}")
        print(f"underlying_score>=50={int(out.underlying_pass50.sum())}/{len(out)}")
        print(f"underlying score median={out.underlying_entry_score.median():.4f} min={out.underlying_entry_score.min():.4f} max={out.underlying_entry_score.max():.4f}")

    print('\n=== PERFORMANCE EFFECT ===')
    print(stat_line('CURRENT_FORCED_SCORE', original_trades))
    print(stat_line('NO_SCORE_FLOOR', no_floor_trades))

    print('\n=== TARGET SOXL 2026-07-02 09:51 ET ===')
    tgt = out[(out.symbol == TARGET_SYMBOL) & (out.entry_time == TARGET_TIME)]
    if tgt.empty:
        # timezone/string fallback for pandas mixed-offset environments
        q = out[(out.symbol == TARGET_SYMBOL) & (out.entry_time.astype(str).str.startswith('2026-07-02 09:51:00'))]
        tgt = q
    if tgt.empty:
        print('TARGET NOT FOUND')
    else:
        cols = [
            'symbol','entry_time','regime','event_score','underlying_entry_score','forced_to_threshold',
            'macd_slope_spread_strength','rsi_slope_strength','macd_gap','gap_delta','rsi','rsi_slope','trend_up',
            'norm_mid_slope_pct'
        ]
        print(tgt[cols].to_string(index=False, float_format=lambda x: f'{x:.4f}'))

    print('\n=== LOWEST UNDERLYING SCORES (first 12) ===')
    cols2 = ['symbol','entry_time','regime','event_score','underlying_entry_score','macd_gap','gap_delta','rsi','rsi_slope','trend_up']
    print(out[cols2].sort_values('underlying_entry_score').head(12).to_string(index=False, float_format=lambda x: f'{x:.4f}'))

    print('\nREADING:')
    print('- If event_score=50 while underlying_entry_score<50, Slow-turn is bypassing the normal score gate.')
    print('- NO_SCORE_FLOOR is not a new strategy; it only removes that bypass and lets the existing simulator threshold act normally.')
    print('- Inspect MACD/RSI acceleration components before deciding any new Slow-turn score formula.')
    print('WROTE', OUT)


if __name__ == '__main__':
    main()
