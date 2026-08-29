from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OPEN_BUY_MINUTE = 9 * 60 + 10
OPENING_ENTRY_END = 10 * 60
PROTECT_MINUTES = 5
BREAKOUT_TIGHT_MINUTES = 10
DD_LEVELS = (0.01, 0.015, 0.02)


def filt_open(ev):
    return {
        ts: rows
        for ts, rows in ev.items()
        if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_BUY_MINUTE
    }


def simulate_5m_hwm(packed_exits, entry_events, state_events, threshold, hwm_dd, hwm_first):
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
            opening_5m = pos['entry_minute'] < OPENING_ENTRY_END and elapsed < PROTECT_MINUTES
            breakout_tight = pos['breakout_entry'] and elapsed < BREAKOUT_TIGHT_MINUTES
            hwm_stop = pos['completed_hwm'] * (1.0 - hwm_dd)

            if minute >= base.FORCE_FLAT_MINUTE:
                close(sym, closep, ts, 'SESSION_FORCE_FLAT')
                continue

            if hwm_first:
                if opening_5m and low <= hwm_stop:
                    close(sym, hwm_stop, ts, f'OPENING_5M_HWM_{hwm_dd*100:.1f}PCT_EXIT')
                    continue
                if low <= pos['stop_price']:
                    close(sym, pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                    continue
            else:
                if low <= pos['stop_price']:
                    close(sym, pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                    continue
                if opening_5m and low <= hwm_stop:
                    close(sym, hwm_stop, ts, f'OPENING_5M_HWM_{hwm_dd*100:.1f}PCT_EXIT')
                    continue

            if breakout_tight and low <= pos['completed_hwm'] * 0.99:
                close(sym, pos['completed_hwm'] * 0.99, ts, 'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
                continue

            if opening_5m or breakout_tight:
                pos['completed_hwm'] = max(pos['completed_hwm'], high)
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


def summarize(label, t):
    print(f'\n=== {label} ===')
    multi.metrics(label, t)
    hwm_mask = t.reason.astype(str).str.startswith('OPENING_5M_HWM_')
    print('OPENING_5M_HWM_EXITS=', int(hwm_mask.sum()))
    print('STRUCTURAL_EXITS=', int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()))
    q = t[(t.symbol.astype(str).str.zfill(6) == '058610') & (pd.to_datetime(t.entry_time) == pd.Timestamp('2026-08-11 09:10:00+09:00'))]
    print('TARGET_058610_2026-08-11_0910:')
    print(q.to_string(index=False) if len(q) else 'NOT_PRESENT')


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))
    empty_waits = pd.DataFrame()
    ev, added, skipped = v17b.build_v17b(ev10, scored, empty_waits)

    print('=== V17C OPENING 5M HWM SWEEP ===')
    print('BUY: 09:00-09:09 blocked only; 09:10+ valid signals tradable immediately.')
    print('TEST: positions entered before 10:00 get completed-HWM protection for first 5 minutes only.')
    print('DD: 1.0%, 1.5%, 2.0%; both STRUCTURAL_FIRST and HWM_FIRST priorities.')
    print('Breakout entries retain existing first-10m completed-HWM -1% rule.')
    print('BREAKOUT_ADDED=', added)
    print('BREAKOUT_SKIPPED=', skipped)

    rows = []
    out_dir = '/home/ubuntu/day-trader-api/engine5_v16_full_validation'

    for hwm_first in (False, True):
        priority = 'HWM_FIRST' if hwm_first else 'STRUCTURAL_FIRST'
        for dd in DD_LEVELS:
            label = f'OPEN5M_{dd*100:.1f}PCT_{priority}'
            t = simulate_5m_hwm(packed, ev, states, THRESHOLD, dd, hwm_first)
            summarize(label, t)
            out = f'{out_dir}/v17c_{label.lower()}.csv'
            t.to_csv(out, index=False)
            print('[CSV]', out)

            wins = int((t.pnl_pct > 0).sum())
            losses = int((t.pnl_pct <= 0).sum())
            gross = float(t.pnl_pct.sum()) if len(t) else 0.0
            avg = float(t.pnl_pct.mean()) if len(t) else 0.0
            pos_sum = float(t.loc[t.pnl_pct > 0, 'pnl_pct'].sum()) if len(t) else 0.0
            neg_sum = float(-t.loc[t.pnl_pct < 0, 'pnl_pct'].sum()) if len(t) else 0.0
            pf = pos_sum / neg_sum if neg_sum > 0 else np.inf
            rows.append({
                'label': label,
                'trades': len(t),
                'wins': wins,
                'losses': losses,
                'win_pct': wins / len(t) * 100.0 if len(t) else 0.0,
                'gross_pct': gross,
                'avg_pct': avg,
                'pf': pf,
                'opening_hwm_exits': int(t.reason.astype(str).str.startswith('OPENING_5M_HWM_').sum()),
                'structural_exits': int((t.reason == 'INITIAL_STRUCTURAL_STOP').sum()),
            })

    summary = pd.DataFrame(rows).sort_values(['gross_pct', 'pf'], ascending=False)
    print('\n=== COMPARISON SORTED BY GROSS ===')
    print(summary.to_string(index=False))
    summary_out = f'{out_dir}/v17c_opening_5m_hwm_sweep_summary.csv'
    summary.to_csv(summary_out, index=False)
    print('[SUMMARY CSV]', summary_out)


if __name__ == '__main__':
    main()
