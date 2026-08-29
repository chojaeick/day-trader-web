from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
MICRO_END_MINUTE = 10 * 60
GAP_PCT = 4.0
FADE_CUT = 0.25
STEP_CUT = 0.35
DOWN_MIN = 2


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def daily_gap_map(raw_bars: pd.DataFrame) -> dict:
    d = raw_bars.copy().sort_values('time')
    d['time'] = pd.to_datetime(d['time'])
    d['date'] = d['time'].dt.date
    days = []
    for day, g in d.groupby('date', sort=True):
        days.append((day, float(g.iloc[0]['open']), float(g.iloc[-1]['close'])))
    out = {}
    for i, (day, op, _) in enumerate(days):
        if i == 0:
            out[day] = np.nan
        else:
            pc = days[i - 1][2]
            out[day] = (op / pc - 1.0) * 100.0 if pc else np.nan
    return out


def build_1m_micro(raw_bars: pd.DataFrame, cfg: DoubleBollingerEngine5Config) -> pd.DataFrame:
    f = raw_bars.copy().sort_values('time').reset_index(drop=True)
    f['time'] = pd.to_datetime(f['time'])
    close = pd.to_numeric(f['close'], errors='coerce').astype(float)
    eng = DoubleBollingerEngine5(cfg)
    macd, signal = eng._macd(close)
    f['macd_slope_1m'] = macd.diff()
    return f[['time','macd_slope_1m']]


def slope_state_at(micro: pd.DataFrame, ts: pd.Timestamp) -> dict:
    t = pd.Timestamp(ts)
    q = micro[(micro['time'] >= t - pd.Timedelta(minutes=5)) & (micro['time'] < t)].copy()
    s = pd.to_numeric(q['macd_slope_1m'], errors='coerce').dropna().to_numpy(dtype=float)
    if len(s) < 4:
        return {'block': False, 'down_steps': 0, 'fade_ratio': np.nan, 'step_ratio': np.nan}
    last4 = s[-4:]
    down_steps = int((np.diff(last4) < 0).sum())
    peak = float(np.max(last4))
    last = float(last4[-1])
    prev = float(last4[-2])
    fade_ratio = last / peak if peak > 0 else np.nan
    step_ratio = last / prev if prev > 0 else np.nan
    block = bool(
        down_steps >= DOWN_MIN
        and np.isfinite(fade_ratio) and fade_ratio <= FADE_CUT
        and np.isfinite(step_ratio) and step_ratio <= STEP_CUT
    )
    return {'block': block, 'down_steps': down_steps, 'fade_ratio': fade_ratio, 'step_ratio': step_ratio}


def apply_v15(frames, raw, cfg):
    micros = {sym: build_1m_micro(raw[sym], cfg) for sym in frames}
    gaps = {sym: daily_gap_map(raw[sym]) for sym in frames}
    out = {}
    blocked_rows = []
    for sym, f0 in frames.items():
        f = f0.copy()
        waits = []
        for r in f.itertuples(index=False):
            ts = pd.Timestamp(r.time)
            minute = ts.hour * 60 + ts.minute
            gap = gaps[sym].get(ts.date(), np.nan)
            sensitive = bool(np.isfinite(gap) and gap >= GAP_PCT and OPEN_MINUTE <= minute < MICRO_END_MINUTE)
            st = slope_state_at(micros[sym], ts) if sensitive else {'block': False, 'down_steps': 0, 'fade_ratio': np.nan, 'step_ratio': np.nan}
            wait = bool(sensitive and st['block'])
            waits.append(wait)
            if wait and bool(getattr(r, 'entry_gate', False)):
                blocked_rows.append((sym, ts, gap, st['down_steps'], st['fade_ratio'], st['step_ratio']))
        f['opening_slope_wait_v15'] = waits
        f['entry_gate_v10_before_v15'] = f['entry_gate'].fillna(False)
        f['entry_gate_v15'] = f['entry_gate_v10_before_v15'] & ~f['opening_slope_wait_v15']
        f['entry_gate'] = f['entry_gate_v15']
        out[sym] = f
    return out, blocked_rows


def run_case(name, packed_exits, state_events, events):
    t, collisions = v8.v7.simulate_v7(packed_exits, events, state_events, THRESHOLD)
    s = summary(name, t)
    print(f"{name}: trades={len(t)} wins={(t.pnl_pct>0).sum()} losses={(t.pnl_pct<=0).sum()} win={s['win_rate']:.2f} avg={s['avg_pct']:.4f} gross={s['gross_pct']:.4f} pf={s['pf']:.3f} collisions={collisions}")
    return t


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))

    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
    f15, blocked_candidates = apply_v15(f10, raw, cfg)

    s10 = reweight(f10, cfg, 0.0)
    s15 = reweight(f15, cfg, 0.0)
    ev10 = filter_open(v8.pack_entry_events(s10))
    ev15 = filter_open(v8.pack_entry_events(s15))

    print('=== CALIBRATED RULE ===')
    print(f'gap>={GAP_PCT:.1f}% and 09:10~09:59: fade_ratio<={FADE_CUT:.2f} AND step_ratio<={STEP_CUT:.2f} AND down_steps>={DOWN_MIN} => WAIT')
    print('\n=== SINGLE-CONFIG REALIZED BACKTEST ===')
    t10 = run_case('B_V10_PLUS_0910', packed_exits, state_events, ev10)
    t15 = run_case('F_V15_CALIBRATED_SLOPE_DECAY', packed_exits, state_events, ev15)

    k10 = set(zip(t10.symbol.astype(str).str.zfill(6), pd.to_datetime(t10.entry_time).astype(str)))
    k15 = set(zip(t15.symbol.astype(str).str.zfill(6), pd.to_datetime(t15.entry_time).astype(str)))
    print('\nREMOVED_REALIZED_FROM_B=', sorted(k10-k15))
    print('ADDED_REALIZED_VIA_PATH_CHANGE=', sorted(k15-k10))

    print('\n=== BLOCKED V10 CANDIDATES BY RULE ===')
    if blocked_candidates:
        for x in blocked_candidates:
            print(x)
    else:
        print('none')

    target = pd.Timestamp('2026-08-11 09:10:00+09:00')
    rows = ev15.get(target, [])
    hit = [r for r in rows if str(r[0]).zfill(6) == '484810']
    print('\nTARGET_484810_2026-08-11_0910_BLOCKED=', not bool(hit))


if __name__ == '__main__':
    main()
