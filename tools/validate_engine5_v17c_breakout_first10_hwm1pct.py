from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17_volume_bypass_tight10 as v17
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUTDIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
OUTDIR.mkdir(parents=True, exist_ok=True)
THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
TIGHT_MINUTES = 10
HWM_DD = 0.01


def filt_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def simulate_unconditional_hwm(packed_exits, entry_events, state_events, threshold):
    """Breakout rule under test:
    - first 10 minutes only: keep full size, 1R hard stop remains, unconditional HWM -1% exits all.
    - after 10 minutes: revert to ordinary Engine5 exit logic.
    To avoid inventing intraminute high/low ordering, the 1% trigger uses HWM from completed prior 1m bars.
    Current bar high is added to HWM only after the current bar survives the trigger.
    """
    pos = None
    trades = []
    current_state = {}
    last_price = None
    last_ts = None

    def realize(frac, price):
        nonlocal pos
        frac = min(float(frac), pos['remaining'])
        if frac <= 0:
            return
        pos['realized'] += frac * (float(price) / pos['entry_price'] - 1.0)
        pos['remaining'] -= frac

    def close_record(price, ts, reason):
        nonlocal pos
        pnl = pos['realized'] + pos['remaining'] * (float(price) / pos['entry_price'] - 1.0)
        trades.append({
            'symbol': pos['symbol'], 'entry_time': pos['entry_time'], 'exit_time': pd.Timestamp(ts),
            'entry_price': pos['entry_price'], 'exit_price': float(price), 'pnl_pct': pnl * 100.0,
            'reason': reason, 'breakout_entry': pos['breakout_entry'],
            'first_tp_done': pos['tp1_done'], 'second_tp_done': pos['tp2_done'],
        })
        pos = None

    for ts, minute, rows in packed_exits:
        last_ts = ts
        if ts in state_events:
            current_state.update(state_events[ts])

        if pos is not None:
            rr = rows.get(pos['symbol'])
            if rr is not None:
                close, low, high, iu, il, ou, spread1, rsi1 = rr
                close = float(close); low = float(low); high = float(high)
                last_price = close
                trend_up, outer_expanding, mid_slope8, spread5, rsi5 = current_state.get(pos['symbol'], (False, False, np.nan, np.nan, np.nan))
                fade_votes = int(np.isfinite(mid_slope8) and mid_slope8 <= 0) + int(np.isfinite(spread5) and spread5 <= 0) + int(np.isfinite(rsi5) and rsi5 <= 0)
                clear_5m_collapse = (not trend_up) and fade_votes >= 2
                fast_fade = np.isfinite(spread1) and spread1 <= 0 and np.isfinite(rsi1) and rsi1 <= 0
                elapsed = (pd.Timestamp(ts) - pos['entry_time']).total_seconds() / 60.0
                tight = pos['breakout_entry'] and elapsed < TIGHT_MINUTES

                if minute >= base.FORCE_FLAT_MINUTE:
                    close_record(close, ts, 'SESSION_FORCE_FLAT')
                elif low <= pos['stop_price']:
                    close_record(pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
                elif tight and low <= pos['completed_hwm'] * (1.0 - HWM_DD):
                    close_record(pos['completed_hwm'] * (1.0 - HWM_DD), ts, 'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
                elif tight:
                    # No TP/ordinary exit during special 10m window. Surviving bar may raise HWM.
                    pos['completed_hwm'] = max(pos['completed_hwm'], high)
                elif not pos['tp1_done']:
                    if high >= pos['tp1_price']:
                        realize(0.50, pos['tp1_price'])
                        pos['tp1_done'] = True
                        pos['tp1_bar_high'] = high
                        pos['post_tp1_high'] = high
                        pos['fade_armed'] = False
                        pos['fast_fade_streak'] = 0
                    elif clear_5m_collapse:
                        close_record(close, ts, 'PRE_TP1_CLEAR_TREND_COLLAPSE')
                else:
                    fresh = high > max(pos['tp1_bar_high'], pos['post_tp1_high'])
                    outer = trend_up and outer_expanding and np.isfinite(ou) and high >= ou
                    if fresh or outer:
                        pos['fade_armed'] = True
                    pos['post_tp1_high'] = max(pos['post_tp1_high'], high)
                    pos['fast_fade_streak'] = pos['fast_fade_streak'] + 1 if pos['fade_armed'] and fast_fade else 0
                    if pos['fade_armed'] and pos['fast_fade_streak'] >= 2:
                        close_record(close, ts, 'FAST_1M_MOMENTUM_FADE_EXIT')
                    else:
                        if pos is not None and (not pos['tp2_done']) and outer:
                            realize(pos['remaining'] * 0.50, ou)
                            pos['tp2_done'] = True
                        if pos is not None and pos['tp2_done'] and np.isfinite(il) and close < il:
                            close_record(close, ts, 'INNER_LOWER_CLOSE_EXIT')

        if pos is None and minute < base.NO_ENTRY_MINUTE:
            cands = entry_events.get(ts)
            if cands:
                eligible = [c for c in cands if c[2] >= float(threshold)]
                if eligible:
                    c = max(eligible, key=lambda x: (x[2], x[3] if np.isfinite(x[3]) else -1e9, x[4] if np.isfinite(x[4]) else -1e9, x[0]))
                    sym, close, score, ms, rs, band_r, stop_dist, entry_iu, entry_il, entry_ou, entry_mid, extended, breakout = c
                    pos = {
                        'symbol': sym, 'entry_time': pd.Timestamp(ts), 'entry_price': float(close),
                        'stop_price': float(close) - float(stop_dist), 'tp1_price': float(close) + 2.0 * float(band_r),
                        'remaining': 1.0, 'realized': 0.0, 'tp1_done': False, 'tp2_done': False,
                        'tp1_bar_high': np.nan, 'post_tp1_high': -np.inf, 'fade_armed': False, 'fast_fade_streak': 0,
                        'breakout_entry': bool(breakout), 'completed_hwm': float(close),
                    }
                    last_price = float(close)

    if pos is not None and last_price is not None and last_ts is not None:
        close_record(last_price, last_ts, 'END_OF_DATA')
    return pd.DataFrame(trades)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))

    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev17b, added, skipped = v17b.build_v17b(ev16, scored, waits)

    print('=== V17C BREAKOUT FIRST-10M EXIT TEST ===')
    print('Entry logic unchanged from V17B. V16 WAIT remains hard veto.')
    print('B=current rule: first10m HWM -1% only when momentum cools.')
    print('C=user rule: first10m unconditional HWM -1%; after 10m ordinary Engine5 exits.')
    print('For 1m OHLC safety, C uses HWM from completed prior minute bars (no invented high-before-low ordering).')
    print('\nV16_WAIT_VETOED=', skipped)
    print('BREAKOUT_CANDIDATES=', added)

    records = []
    for sym, ts, price, vol_ratio in added:
        ts = pd.Timestamp(ts)
        es = [e for e in ev17b.get(ts, []) if str(e[0]).zfill(6) == str(sym).zfill(6) and bool(e[-1])]
        if not es:
            continue
        one = {ts: [es[0]]}
        old, _ = v17.simulate_v17(packed_exits, one, state_events, THRESHOLD)
        new = simulate_unconditional_hwm(packed_exits, one, state_events, THRESHOLD)
        ro = old.iloc[0] if len(old) else None
        rn = new.iloc[0] if len(new) else None
        rec = {
            'symbol': str(sym).zfill(6), 'candidate_time': ts, 'candidate_price': float(price), 'volume_ratio_prev5m': float(vol_ratio),
            'old_exit_time': pd.Timestamp(ro.exit_time) if ro is not None else pd.NaT,
            'old_pnl_pct': float(ro.pnl_pct) if ro is not None else np.nan,
            'old_reason': str(ro.reason) if ro is not None else 'NO_TRADE',
            'new_exit_time': pd.Timestamp(rn.exit_time) if rn is not None else pd.NaT,
            'new_pnl_pct': float(rn.pnl_pct) if rn is not None else np.nan,
            'new_reason': str(rn.reason) if rn is not None else 'NO_TRADE',
            'delta_pnl_pct': (float(rn.pnl_pct) - float(ro.pnl_pct)) if (rn is not None and ro is not None) else np.nan,
        }
        records.append(rec)
        print(f'\n--- {sym} {ts} vol={vol_ratio:.3f}x entry={price:.2f} ---')
        if ro is not None:
            print(f'B_CURRENT exit={pd.Timestamp(ro.exit_time)} pnl={float(ro.pnl_pct):+.4f}% reason={ro.reason}')
        if rn is not None:
            print(f'C_USER10M exit={pd.Timestamp(rn.exit_time)} pnl={float(rn.pnl_pct):+.4f}% reason={rn.reason}')
        if ro is not None and rn is not None:
            print(f'DELTA={float(rn.pnl_pct)-float(ro.pnl_pct):+.4f}%p')

    df = pd.DataFrame(records)
    print('\n=== SUMMARY ===')
    if df.empty:
        print('No non-vetoed breakout candidates.')
    else:
        for label, col in [('B_CURRENT','old_pnl_pct'), ('C_USER10M','new_pnl_pct')]:
            p = pd.to_numeric(df[col], errors='coerce').dropna()
            wins = int((p > 0).sum())
            print(f'{label}: n={len(p)} wins={wins} win_rate={(wins/len(p)*100 if len(p) else 0):.2f}% gross={p.sum():+.4f}% avg={p.mean():+.4f}%')
        d = pd.to_numeric(df['delta_pnl_pct'], errors='coerce').dropna()
        print(f'C_MINUS_B: gross_delta={d.sum():+.4f}%p avg_delta={d.mean():+.4f}%p improved={(d>0).sum()}/{len(d)}')
        print('\nRESULT TABLE')
        print(df.to_string(index=False))

    out = OUTDIR / 'v17c_breakout_first10_hwm1pct.csv'
    df.to_csv(out, index=False)
    print('\n[CSV]', out)


if __name__ == '__main__':
    main()
