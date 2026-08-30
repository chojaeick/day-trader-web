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
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_early_ratchet_sweep')
FEE = integ.FEE_RT_PCT
THRESHOLD = integ.THRESHOLD

# Baseline plus four causal variants.
# Ratchet rule: during first N minutes, move the original stop upward 1:1 by
# the PRIOR COMPLETED high's favorable move from entry. Never move it down.
# HOLD keeps the highest ratcheted stop after the window; REVERT returns to
# the original V22 structural stop after the window.
MATRIX = [
    ('A', None, None),
    ('R5_HOLD', 5.0, 'HOLD'),
    ('R5_REVERT', 5.0, 'REVERT'),
    ('R10_HOLD', 10.0, 'HOLD'),
    ('R10_REVERT', 10.0, 'REVERT'),
]


def n(x):
    return str(x).zfill(6)


def stats(label, tr):
    g = pd.to_numeric(tr.pnl_pct, errors='coerce').dropna() if len(tr) else pd.Series(dtype=float)
    net = g - FEE
    gp = float(net[net > 0].sum()) if len(net) else 0.0
    gl = float(-net[net < 0].sum()) if len(net) else 0.0
    return dict(
        case=label,
        trades=len(net),
        wins=int((net > 0).sum()),
        win_pct=float((net > 0).mean() * 100) if len(net) else 0.0,
        net_sum_pct=float(net.sum()) if len(net) else 0.0,
        avg_net_pct=float(net.mean()) if len(net) else 0.0,
        pf=(gp / gl if gl > 0 else np.inf),
        max_loss_pct=float(net.min()) if len(net) else np.nan,
        max_win_pct=float(net.max()) if len(net) else np.nan,
    )


def simulate_variant(packed, state_events, tagged, ratchet_minutes=None, post_policy=None):
    by_time = {}
    for x in tagged:
        by_time.setdefault(pd.Timestamp(x['time']), []).append(x)

    positions, trades, current_state, last_price = {}, [], {}, {}
    last_ts = None

    def realize(pos, frac, price):
        frac = min(float(frac), pos['remaining'])
        if frac <= 0:
            return
        pos['realized'] += frac * (float(price) / pos['entry_price'] - 1.0)
        pos['remaining'] -= frac

    def original_stop(pos):
        if pos['source'] == 'V_REBOUND':
            s = pos['v_structural_stop']
        else:
            s = pos['stop_price']
        return float(s) if np.isfinite(s) else np.nan

    def close_pos(sym, price, ts, reason):
        pos = positions[sym]
        pnl = pos['realized'] + pos['remaining'] * (float(price) / pos['entry_price'] - 1.0)
        trades.append(dict(
            symbol=sym,
            entry_time=pos['entry_time'],
            exit_time=pd.Timestamp(ts),
            entry_price=pos['entry_price'],
            exit_price=float(price),
            pnl_pct=pnl * 100.0,
            reason=reason,
            source=pos['source'],
            tp1_done=pos['tp1_done'],
            ratchet_exit=(reason == 'EARLY_RATCHET_STOP'),
            ratchet_peak_pct=(pos['ratchet_peak'] / pos['entry_price'] - 1.0) * 100.0,
            ratchet_stop_pct=(pos['ratchet_stop'] / pos['entry_price'] - 1.0) * 100.0 if np.isfinite(pos['ratchet_stop']) else np.nan,
        ))
        del positions[sym]

    for ts, minute, rows in packed:
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
            fast_fade = np.isfinite(spread1) and spread1 <= 0 and np.isfinite(rsi1) and rsi1 <= 0
            elapsed = (pd.Timestamp(ts) - pos['entry_time']).total_seconds() / 60.0
            tight = pos['breakout_entry'] and elapsed < multi.TIGHT_MINUTES
            gross_ret = (closep / pos['entry_price'] - 1.0) * 100.0
            if pos['source'] == 'V_REBOUND' and not pos['run_mode'] and gross_ret >= integ.RUN_ACTIVATE_PCT:
                pos['run_mode'] = True

            # CAUSAL ratchet: use only highs from PREVIOUS completed bars for the
            # stop applied to this bar. This avoids assuming high occurred before
            # low inside the same 1-minute candle.
            ratchet_active = ratchet_minutes is not None and elapsed < float(ratchet_minutes)
            ratchet_stop = np.nan
            if ratchet_minutes is not None:
                base_stop = original_stop(pos)
                if np.isfinite(base_stop):
                    candidate = base_stop + max(0.0, pos['ratchet_peak'] - pos['entry_price'])
                    pos['ratchet_stop'] = max(pos['ratchet_stop'], candidate) if np.isfinite(pos['ratchet_stop']) else candidate
                    if ratchet_active:
                        ratchet_stop = pos['ratchet_stop']
                    elif post_policy == 'HOLD':
                        ratchet_stop = pos['ratchet_stop']

            if minute >= base.FORCE_FLAT_MINUTE:
                close_pos(sym, closep, ts, 'SESSION_FORCE_FLAT')
            elif np.isfinite(ratchet_stop) and low <= ratchet_stop:
                close_pos(sym, ratchet_stop, ts, 'EARLY_RATCHET_STOP')
            elif pos['source'] == 'V_REBOUND' and (not pos['run_mode']) and low <= pos['v_structural_stop']:
                close_pos(sym, pos['v_structural_stop'], ts, 'V_HIGHER_LOW_STRUCTURAL_STOP')
            elif pos['source'] != 'V_REBOUND' and low <= pos['stop_price']:
                close_pos(sym, pos['stop_price'], ts, 'INITIAL_STRUCTURAL_STOP')
            elif pos['source'] == 'V_REBOUND' and pos['run_mode']:
                # Preserve current V22 semantics after the early ratchet check.
                if ratchet_active:
                    pos['ratchet_peak'] = max(pos['ratchet_peak'], high)
                continue
            elif tight and low <= pos['completed_hwm'] * (1.0 - multi.HWM_DD):
                close_pos(sym, pos['completed_hwm'] * (1.0 - multi.HWM_DD), ts, 'BREAKOUT_FIRST10_HWM_1PCT_EXIT')
            elif tight:
                pos['completed_hwm'] = max(pos['completed_hwm'], high)
                if ratchet_active:
                    pos['ratchet_peak'] = max(pos['ratchet_peak'], high)
            elif not pos['tp1_done']:
                if high >= pos['tp1_price']:
                    realize(pos, 0.50, pos['tp1_price'])
                    pos['tp1_done'] = True
                    pos['tp1_bar_high'] = high
                    pos['post_tp1_high'] = high
                    pos['fade_armed'] = False
                    pos['fast_fade_streak'] = 0
                elif clear_5m_collapse:
                    close_pos(sym, closep, ts, 'PRE_TP1_CLEAR_TREND_COLLAPSE')
            else:
                fresh = high > max(pos['tp1_bar_high'], pos['post_tp1_high'])
                outer = trend_up and outer_expanding and np.isfinite(ou) and high >= ou
                if fresh or outer:
                    pos['fade_armed'] = True
                pos['post_tp1_high'] = max(pos['post_tp1_high'], high)
                pos['fast_fade_streak'] = pos['fast_fade_streak'] + 1 if pos['fade_armed'] and fast_fade else 0
                if pos['fade_armed'] and pos['fast_fade_streak'] >= 2:
                    close_pos(sym, closep, ts, 'FAST_1M_MOMENTUM_FADE_EXIT')
                else:
                    if sym in positions and (not pos['tp2_done']) and outer:
                        realize(pos, pos['remaining'] * 0.50, ou)
                        pos['tp2_done'] = True
                    if sym in positions and pos['tp2_done'] and np.isfinite(il) and closep < il:
                        close_pos(sym, closep, ts, 'INNER_LOWER_CLOSE_EXIT')

            if sym in positions and ratchet_active:
                # Update only AFTER this bar's exit logic for next-bar causality.
                positions[sym]['ratchet_peak'] = max(positions[sym]['ratchet_peak'], high)

        if minute < base.NO_ENTRY_MINUTE:
            for item in by_time.get(pd.Timestamp(ts), []):
                sym = item['symbol']
                c = item['event']
                if sym in positions or c[2] < float(THRESHOLD):
                    continue
                _, closep, score, msv, rsv, band_r, stop_dist, entry_iu, entry_il, entry_ou, entry_mid, extended, breakout = c
                entry = float(closep)
                positions[sym] = dict(
                    symbol=sym,
                    entry_time=pd.Timestamp(ts),
                    entry_price=entry,
                    stop_price=entry - float(stop_dist),
                    tp1_price=entry + 2.0 * float(band_r),
                    remaining=1.0,
                    realized=0.0,
                    tp1_done=False,
                    tp2_done=False,
                    tp1_bar_high=np.nan,
                    post_tp1_high=-np.inf,
                    fade_armed=False,
                    fast_fade_streak=0,
                    breakout_entry=bool(breakout),
                    completed_hwm=entry,
                    source=item['source'],
                    v_structural_stop=float(item['meta'].get('structural_stop', np.nan)),
                    run_mode=False,
                    ratchet_peak=entry,
                    ratchet_stop=np.nan,
                )
                last_price[sym] = entry

    if last_ts is not None:
        for sym in list(positions):
            if sym in last_price:
                close_pos(sym, last_price[sym], last_ts, 'END_OF_DATA')
    return pd.DataFrame(trades)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR EARLY STOP RATCHET SWEEP ===', flush=True)
    print('Rule: first N minutes only, raise original stop 1:1 by prior completed-bar MFE.', flush=True)
    print('Compare HOLD vs REVERT after the 5m/10m window.', flush=True)

    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)

    baseline = integ.simulate(packed, states, tagged)
    b = stats('A_EXPECTED', baseline)
    print('BASELINE current integrated:', b, flush=True)

    rows, alltr = [], []
    for name, mins, policy in MATRIX:
        tr = simulate_variant(packed, states, tagged, mins, policy)
        st = stats(name, tr)
        rows.append(st)
        x = tr.copy()
        x['case'] = name
        alltr.append(x)
        print(name, st, flush=True)

    summary = pd.DataFrame(rows)
    trades = pd.concat(alltr, ignore_index=True) if alltr else pd.DataFrame()

    a = summary[summary.case == 'A'].iloc[0]
    tol = 1e-9
    repro = (
        int(a.trades) == int(b['trades'])
        and abs(float(a.net_sum_pct) - float(b['net_sum_pct'])) < tol
        and abs(float(a.max_loss_pct) - float(b['max_loss_pct'])) < tol
    )
    print('\nREPRO CHECK A == CURRENT V22:', 'PASS' if repro else 'FAIL')
    if not repro:
        raise SystemExit('A baseline mismatch; sweep invalid.')

    summary.to_csv(OUT / 'summary.csv', index=False)
    trades.to_csv(OUT / 'trades.csv', index=False)

    print('\n=== SUMMARY ===')
    print(summary.to_string(index=False))
    print('\n=== RATCHET EXIT COUNTS ===')
    print(trades.groupby('case').ratchet_exit.sum().to_string())
    print('\n=== RATCHET EXITS BY SOURCE ===')
    q = trades[trades.ratchet_exit]
    print(q.groupby(['case', 'source']).size().to_string() if len(q) else 'NONE')
    print('\n=== RATCHET EXIT DETAILS ===')
    cols = ['case','source','symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','ratchet_peak_pct','ratchet_stop_pct']
    print(q[cols].sort_values(['case','entry_time']).to_string(index=False) if len(q) else 'NONE')
    print('\n=== TP1 DONE COUNTS ===')
    print(trades.groupby('case').tp1_done.sum().to_string())
    print('WROTE', OUT / 'summary.csv')
    print('WROTE', OUT / 'trades.csv')


if __name__ == '__main__':
    main()
