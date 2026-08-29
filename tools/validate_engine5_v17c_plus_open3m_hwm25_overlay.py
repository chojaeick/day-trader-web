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
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OPENING_ENTRY_END = 10 * 60
PROTECT_MINUTES = 3
HWM_DD = 0.025
BREAKOUT_TIGHT_MINUTES = 10
OUT_DIR = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'


def simulate_overlay(packed_exits, entry_events, state_events, threshold):
    positions = {}
    trades = []
    current_state = {}
    last_price = {}
    last_ts = None

    def realize(pos, frac, price):
        frac = min(float(frac), pos['remaining'])
        if frac <= 0:
            return
        pos['realized'] += frac * (float(price) / pos['entry_price'] - 1.0)
        pos['remaining'] -= frac

    def close(sym, price, ts, reason):
        pos = positions[sym]
        pnl = pos['realized'] + pos['remaining'] * (float(price) / pos['entry_price'] - 1.0)
        trades.append({
            'symbol': sym,
            'entry_time': pos['entry_time'],
            'exit_time': pd.Timestamp(ts),
            'entry_price': pos['entry_price'],
            'exit_price': float(price),
            'pnl_pct': pnl * 100.0,
            'reason': reason,
            'breakout_entry': pos['breakout_entry'],
            'first_tp_done': pos['tp1_done'],
            'second_tp_done': pos['tp2_done'],
        })
        del positions[sym]

    for ts, minute, rows in packed_exits:
        last_ts = ts
        if ts in state_events:
            current_state.update(state_events[ts])

        for sym in list(positions):
            pos = positions.get(sym)
            rr = rows.get(sym)
            if pos is None or rr is None:
                continue

            closep, low, high, iu, il, ou, spread1, rsi1 = rr
            closep = float(closep)
            low = float(low)
            high = float(high)
            last_price[sym] = closep

            trend_up, outer_expanding, mid_slope8, spread5, rsi5 = current_state.get(
                sym, (False, False, np.nan, np.nan, np.nan)
            )
            fade_votes = (
                int(np.isfinite(mid_slope8) and mid_slope8 <= 0)
                + int(np.isfinite(spread5) and spread5 <= 0)
                + int(np.isfinite(rsi5) and rsi5 <= 0)
            )
            clear_5m_collapse = (not trend_up) and fade_votes >= 2
            fast_fade = (
                np.isfinite(spread1) and spread1 <= 0
                and np.isfinite(rsi1) and rsi1 <= 0
            )

            elapsed = (pd.Timestamp(ts) - pos['entry_time']).total_seconds() / 60.0
            opening_overlay = (
                pos['entry_minute'] < OPENING_ENTRY_END
                and elapsed < PROTECT_MINUTES
            )
            breakout_tight = pos['breakout_entry'] and elapsed < BREAKOUT_TIGHT_MINUTES

            if minute >= base.FORCE_FLAT_MINUTE:
                close(sym, closep, ts, 'SESSION_FORCE_FLAT')
                continue

            # Added risk overlay: first 3 minutes, completed-HWM -2.5%, HWM_FIRST.
            if opening_overlay:
                hwm_stop = pos['completed_hwm'] * (1.0 - HWM_DD)
                if low <= hwm_stop:
                    close(sym, hwm_stop, ts, 'OPENING_3M_HWM_2.5PCT_EXIT')
                    continue

            # Existing V17C structural stop remains immediately after overlay check.
            if low <= pos['stop_price']:
                close(sym, pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                continue

            # Existing breakout first-10m HWM -1% behavior retained.
            if breakout_tight and low <= pos['completed_hwm'] * 0.99:
                close(sym, pos['completed_hwm'] * 0.99, ts, 'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
                continue

            # Update completed HWM only after stop checks, preventing current-bar lookahead.
            if opening_overlay or breakout_tight:
                pos['completed_hwm'] = max(pos['completed_hwm'], high)

            # Preserve original V17C breakout-tight behavior: ordinary exits are suppressed
            # during its first 10 minutes. Opening overlay itself does NOT suppress exits.
            if breakout_tight:
                continue

            if not pos['tp1_done']:
                if high >= pos['tp1_price']:
                    realize(pos, 0.50, pos['tp1_price'])
                    pos['tp1_done'] = True
                    pos['tp1_bar_high'] = high
                    pos['post_tp1_high'] = high
                    pos['fade_armed'] = False
                    pos['fast_fade_streak'] = 0
                elif clear_5m_collapse:
                    close(sym, closep, ts, 'PRE_TP1_CLEAR_TREND_COLLAPSE')
            else:
                fresh = high > max(pos['tp1_bar_high'], pos['post_tp1_high'])
                outer = trend_up and outer_expanding and np.isfinite(ou) and high >= ou
                if fresh or outer:
                    pos['fade_armed'] = True
                pos['post_tp1_high'] = max(pos['post_tp1_high'], high)
                pos['fast_fade_streak'] = (
                    pos['fast_fade_streak'] + 1 if pos['fade_armed'] and fast_fade else 0
                )
                if pos['fade_armed'] and pos['fast_fade_streak'] >= 2:
                    close(sym, closep, ts, 'FAST_1M_MOMENTUM_FADE_EXIT')
                else:
                    if sym in positions and (not pos['tp2_done']) and outer:
                        realize(pos, pos['remaining'] * 0.50, ou)
                        pos['tp2_done'] = True
                    if sym in positions and pos['tp2_done'] and np.isfinite(il) and closep < il:
                        close(sym, closep, ts, 'INNER_LOWER_CLOSE_EXIT')

        if minute < base.NO_ENTRY_MINUTE:
            for c in entry_events.get(ts, []):
                sym = str(c[0]).zfill(6)
                if sym in positions or c[2] < float(threshold):
                    continue
                _, closep, score, ms, rs, band_r, stop_dist, entry_iu, entry_il, entry_ou, entry_mid, extended, breakout = c
                positions[sym] = {
                    'symbol': sym,
                    'entry_time': pd.Timestamp(ts),
                    'entry_minute': minute,
                    'entry_price': float(closep),
                    'stop_price': float(closep) - float(stop_dist),
                    'tp1_price': float(closep) + 2.0 * float(band_r),
                    'remaining': 1.0,
                    'realized': 0.0,
                    'tp1_done': False,
                    'tp2_done': False,
                    'tp1_bar_high': np.nan,
                    'post_tp1_high': -np.inf,
                    'fade_armed': False,
                    'fast_fade_streak': 0,
                    'breakout_entry': bool(breakout),
                    'completed_hwm': float(closep),
                }
                last_price[sym] = float(closep)

    if last_ts is not None:
        for sym in list(positions):
            if sym in last_price:
                close(sym, last_price[sym], last_ts, 'END_OF_DATA')

    return pd.DataFrame(trades)


def stat_row(label, t):
    p = pd.to_numeric(t.pnl_pct, errors='coerce').dropna() if len(t) else pd.Series(dtype=float)
    gp = float(p[p > 0].sum()) if len(p) else 0.0
    gl = float(-p[p < 0].sum()) if len(p) else 0.0
    return {
        'label': label,
        'trades': len(p),
        'wins': int((p > 0).sum()),
        'losses': int((p <= 0).sum()),
        'win_pct': float((p > 0).mean() * 100.0) if len(p) else 0.0,
        'gross_pct': float(p.sum()) if len(p) else 0.0,
        'avg_pct': float(p.mean()) if len(p) else 0.0,
        'pf': gp / gl if gl > 0 else np.inf,
        'maxloss_pct': float(p.min()) if len(p) else np.nan,
        'opening_hwm_exits': int(t.reason.astype(str).str.startswith('OPENING_3M_HWM_').sum()) if len(t) else 0,
        'structural_exits': int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()) if len(t) else 0,
    }


def main():
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

    # Exact ORIGINAL V17C entry stream: V16 WAIT/reaccel + V17B breakout/veto.
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, added, skipped = v17b.build_v17b(ev16, scored, waits)

    original = multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)
    overlay = simulate_overlay(packed, ev_v17c, states, THRESHOLD)

    print('=== ORIGINAL V17C VS V17C + OPENING 3M HWM 2.5% OVERLAY ===')
    print('ENTRY STREAM: identical V16 WAIT/reaccel + V17B breakout/veto events.')
    print('OVERLAY: entries before 10:00 only; first 3 minutes; completed-HWM -2.5%; HWM_FIRST.')
    print('IMPORTANT: opening overlay does NOT suppress ordinary V17C exits.')
    print('Existing breakout first-10m completed-HWM -1% behavior remains unchanged.')
    print('BREAKOUT_ADDED=', added)
    print('BREAKOUT_SKIPPED=', skipped)

    print('\n=== ORIGINAL_V17C ===')
    multi.metrics('ORIGINAL_V17C', original)
    print('STRUCTURAL_EXITS=', int((original.reason == 'INITIAL_STRUCTURAL_STOP').sum()))

    print('\n=== V17C_PLUS_OPEN3M_HWM25_OVERLAY ===')
    multi.metrics('V17C_PLUS_OPEN3M_HWM25_OVERLAY', overlay)
    print('OPENING_3M_HWM_EXITS=', int(overlay.reason.astype(str).str.startswith('OPENING_3M_HWM_').sum()))
    print('STRUCTURAL_EXITS=', int((overlay.reason == 'INITIAL_STRUCTURAL_STOP').sum()))

    rows = pd.DataFrame([
        stat_row('ORIGINAL_V17C', original),
        stat_row('V17C_PLUS_OPEN3M_HWM25_OVERLAY', overlay),
    ])
    b = rows.iloc[0]
    x = rows.iloc[1]

    print('\n=== DIRECT COMPARISON ===')
    print(rows.to_string(index=False))
    print('\n=== DELTAS ===')
    print('GROSS_DELTA=', f'{x.gross_pct - b.gross_pct:+.6f}pp')
    print('PF_DELTA=', f'{x.pf - b.pf:+.6f}')
    print('AVG_DELTA=', f'{x.avg_pct - b.avg_pct:+.6f}pp/trade')
    print('WIN_RATE_DELTA=', f'{x.win_pct - b.win_pct:+.6f}pp')
    print('TRADE_COUNT_DELTA=', int(x.trades - b.trades))
    print('MAXLOSS_DELTA=', f'{x.maxloss_pct - b.maxloss_pct:+.6f}pp')

    # Show only changed matched entries to explain the delta.
    keys = ['symbol', 'entry_time']
    a = original.copy(); z = overlay.copy()
    a['symbol'] = a.symbol.astype(str).str.zfill(6); z['symbol'] = z.symbol.astype(str).str.zfill(6)
    a['entry_time'] = pd.to_datetime(a.entry_time); z['entry_time'] = pd.to_datetime(z.entry_time)
    m = a.merge(z, on=keys, how='outer', suffixes=('_orig', '_overlay'), indicator=True)
    both = m[m['_merge'] == 'both'].copy()
    changed = both[(both.reason_orig != both.reason_overlay) | (np.abs(both.pnl_pct_orig - both.pnl_pct_overlay) > 1e-12)]
    print('\n=== CHANGED MATCHED ENTRIES ===')
    cols = ['symbol','entry_time','exit_time_orig','pnl_pct_orig','reason_orig','exit_time_overlay','pnl_pct_overlay','reason_overlay']
    print(changed[cols].sort_values(['entry_time','symbol']).to_string(index=False) if len(changed) else 'NONE')
    print('CHANGED_MATCHED_COUNT=', len(changed))
    print('ORIGINAL_ONLY_ENTRIES=', int((m['_merge'] == 'left_only').sum()))
    print('OVERLAY_ONLY_ENTRIES=', int((m['_merge'] == 'right_only').sum()))

    original.to_csv(f'{OUT_DIR}/v17c_original_for_open3m_overlay_compare.csv', index=False)
    overlay.to_csv(f'{OUT_DIR}/v17c_plus_open3m_hwm25_overlay.csv', index=False)
    rows.to_csv(f'{OUT_DIR}/v17c_plus_open3m_hwm25_overlay_summary.csv', index=False)
    print('\n[CSV]', f'{OUT_DIR}/v17c_plus_open3m_hwm25_overlay_summary.csv')


if __name__ == '__main__':
    main()
