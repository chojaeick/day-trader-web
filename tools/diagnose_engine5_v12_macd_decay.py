from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_fast_tuner_v9 as v9
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config

# Diagnostic only: compare the exact intended V10 baseline (including 09:10 open block)
# against a V12 candidate that rejects a clearly decaying *actual MACD slope*.
# This is deliberately NOT a 360-config sweep.
CFG_NAME = 'S_M2.0_R1.5'
THRESHOLD = 50
OPEN_DECAY_END_MINUTE = 10 * 60
GAP_RISK_PCT = 4.0

ORIG_BUILD_CFG_FRAMES = base.build_cfg_frames


def daily_gap_map(raw_bars: pd.DataFrame) -> dict:
    d = raw_bars.copy().sort_values('time')
    d['time'] = pd.to_datetime(d['time'])
    d['date'] = d['time'].dt.date
    days = [(day, float(g.iloc[0]['open']), float(g.iloc[-1]['close'])) for day, g in d.groupby('date', sort=True)]
    out = {}
    for i, (day, op, _) in enumerate(days):
        if i == 0:
            out[day] = np.nan
        else:
            pc = days[i - 1][2]
            out[day] = (op / pc - 1.0) * 100.0 if pc else np.nan
    return out


def apply_v12_decay_gate(f: pd.DataFrame, gap_map: dict) -> pd.DataFrame:
    z = f.copy()
    ts = pd.to_datetime(z['time'])
    minute = ts.dt.hour * 60 + ts.dt.minute
    z['gap_pct'] = ts.dt.date.map(gap_map).astype(float)

    ms = pd.to_numeric(z['macd_slope'], errors='coerce')
    p1, p2, p3 = ms.shift(1), ms.shift(2), ms.shift(3)
    recent_peak = pd.concat([p1, p2, p3], axis=1).max(axis=1)
    fade_ratio = ms / recent_peak.replace(0.0, np.nan)

    # Core pattern from manual chart validation:
    # MACD itself is still rising (>0), but its rise per 5m bar has already peaked
    # and has weakened for two consecutive completed bars. A merely positive
    # MACD-vs-signal spread must not override this state.
    two_step_decay = (
        (ms > 0)
        & (p1 > 0)
        & (p2 > 0)
        & (ms < p1)
        & (p1 < p2)
    ).fillna(False)
    material_peak_fade = (fade_ratio <= 0.85).fillna(False)

    opening_gap_risk = (
        (z['gap_pct'] >= GAP_RISK_PCT)
        & (minute < OPEN_DECAY_END_MINUTE)
    ).fillna(False)

    # For a large opening gap, either two-step decay or a severe collapse from the
    # recent MACD-slope peak is enough to force WAIT. Severe collapse requires the
    # current slope to remain positive so this is specifically "fading rise",
    # not a duplicate downtrend rule.
    severe_peak_fade = (
        (ms > 0)
        & recent_peak.gt(0)
        & (fade_ratio <= 0.55)
    ).fillna(False)

    z['macd_slope_recent_peak'] = recent_peak
    z['macd_slope_fade_ratio'] = fade_ratio
    z['macd_slope_two_step_decay'] = two_step_decay
    z['opening_gap_risk'] = opening_gap_risk
    z['macd_decay_wait_v12'] = opening_gap_risk & (two_step_decay | severe_peak_fade | (material_peak_fade & (ms < p1)))

    # V12 can only remove V10 BUY candidates; it cannot create one.
    z['entry_gate_v10_before_v12'] = z['entry_gate'].fillna(False)
    z['entry_gate_v12'] = z['entry_gate_v10_before_v12'] & ~z['macd_decay_wait_v12']
    z['entry_gate'] = z['entry_gate_v12']
    return z


def pack_0910(frames):
    return v9.pack_entry_events(frames)


def simulate(frames, raw, cfg):
    packed_exits = v8.base.pack_exit_events(raw, cfg)
    entry_events = pack_0910(frames)
    state_events = base.pack_state_events(frames)
    return v8.v7.simulate_v7(packed_exits, entry_events, state_events, THRESHOLD)


def stats(name, t, collisions):
    wins = int((t.pnl_pct > 0).sum()) if len(t) else 0
    losses = int((t.pnl_pct <= 0).sum()) if len(t) else 0
    win = wins / len(t) * 100.0 if len(t) else 0.0
    avg = float(t.pnl_pct.mean()) if len(t) else 0.0
    gross = float(t.pnl_pct.sum()) if len(t) else 0.0
    pos = float(t.loc[t.pnl_pct > 0, 'pnl_pct'].sum()) if len(t) else 0.0
    neg = -float(t.loc[t.pnl_pct < 0, 'pnl_pct'].sum()) if len(t) else 0.0
    pf = pos / neg if neg > 0 else float('inf')
    print(f'{name}: trades={len(t)} wins={wins} losses={losses} win={win:.2f} avg={avg:.4f} gross={gross:.4f} pf={pf:.3f} collisions={collisions}')


def main():
    raw = base.load_data()
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    raw_frames = ORIG_BUILD_CFG_FRAMES(raw, cfg)
    frames_b = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    frames_d = {s: apply_v12_decay_gate(frames_b[s], daily_gap_map(raw[s])) for s in frames_b}

    print('=== 484810 2026-08-11 09:00~09:30 MACD DECAY TRACE ===')
    f = frames_d['484810'].copy()
    tt = pd.to_datetime(f['time'])
    q = f[(tt >= pd.Timestamp('2026-08-11 09:00', tz='Asia/Seoul')) & (tt <= pd.Timestamp('2026-08-11 09:30', tz='Asia/Seoul'))].copy()
    cols = ['time','close','gap_pct','macd','macd_signal','macd_slope','macd_signal_slope','macd_slope_spread','macd_slope_recent_peak','macd_slope_fade_ratio','rsi','rsi_slope','entry_gate_v10_before_v12','macd_decay_wait_v12','entry_gate_v12']
    print(q[cols].to_string(index=False))

    target = q[pd.to_datetime(q['time']) == pd.Timestamp('2026-08-11 09:10', tz='Asia/Seoul')]
    print('\n=== TARGET REGRESSION ===')
    if target.empty:
        print('TARGET_ROW_NOT_FOUND')
    else:
        r = target.iloc[0]
        print(f"gap={r['gap_pct']:.2f}% macd_slope={r['macd_slope']:.6f} recent_peak={r['macd_slope_recent_peak']:.6f} fade_ratio={r['macd_slope_fade_ratio']:.3f}")
        print(f"V10_candidate={bool(r['entry_gate_v10_before_v12'])} decay_wait={bool(r['macd_decay_wait_v12'])} V12_candidate={bool(r['entry_gate_v12'])}")
        print('REGRESSION_PASS=', bool(r['entry_gate_v10_before_v12']) and bool(r['macd_decay_wait_v12']) and not bool(r['entry_gate_v12']))

    print('\n=== SINGLE-CONFIG REALIZED BACKTEST ===')
    tb, cb = simulate(frames_b, raw, cfg)
    td, cd = simulate(frames_d, raw, cfg)
    stats('B_V10_PLUS_0910', tb, cb)
    stats('D_V12_MACD_DECAY', td, cd)

    # Show exactly which realized trades changed. A changed prior position can alter
    # later realized entries, so compare by symbol+entry_time rather than counts only.
    kb = set(zip(tb.symbol.astype(str), pd.to_datetime(tb.entry_time).astype(str)))
    kd = set(zip(td.symbol.astype(str), pd.to_datetime(td.entry_time).astype(str)))
    print('\nREMOVED_REALIZED_FROM_B=', sorted(kb - kd))
    print('ADDED_REALIZED_VIA_PATH_CHANGE=', sorted(kd - kb))


if __name__ == '__main__':
    main()
