from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as b
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
MICRO_END_MINUTE = 10 * 60
GAP_PCT = 4.0
TARGET = pd.Timestamp('2026-08-11 09:10:00+09:00')
TARGET_SYMBOL = '484810'


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
    f['macd_1m'] = macd
    f['signal_1m'] = signal
    f['macd_slope_1m'] = macd.diff()
    f['signal_slope_1m'] = signal.diff()
    f['spread_1m'] = f['macd_slope_1m'] - f['signal_slope_1m']
    rsi = eng._rsi(close, cfg.rsi_period)
    f['rsi_1m'] = rsi
    f['rsi_slope_1m'] = rsi.diff()
    return f


def micro_state_at(micro: pd.DataFrame, ts: pd.Timestamp) -> dict:
    # A 5m signal stamped 09:10 is based on the completed 09:05~09:09 interval.
    # Therefore use only 1m bars strictly before ts; no look-ahead.
    t = pd.Timestamp(ts)
    q = micro[(micro['time'] >= t - pd.Timedelta(minutes=5)) & (micro['time'] < t)].copy()
    s = pd.to_numeric(q['macd_slope_1m'], errors='coerce').dropna().to_numpy(dtype=float)
    sp = pd.to_numeric(q['spread_1m'], errors='coerce').dropna().to_numpy(dtype=float)
    rs = pd.to_numeric(q['rsi_slope_1m'], errors='coerce').dropna().to_numpy(dtype=float)
    if len(s) < 4:
        return {'decay': False, 'n': len(s), 'last3_down': False, 'down_steps': 0, 'trend': np.nan,
                'peak': np.nan, 'last': np.nan, 'fade_ratio': np.nan, 'spread_last': np.nan,
                'rsi_last': np.nan, 'frame': q}

    last4 = s[-4:]
    diffs = np.diff(last4)
    down_steps = int((diffs < 0).sum())
    x = np.arange(len(last4), dtype=float)
    trend = float(np.polyfit(x, last4, 1)[0])
    peak = float(np.max(last4))
    last = float(last4[-1])
    fade_ratio = last / peak if peak > 0 else np.nan
    last3_down = bool(last4[-3] > last4[-2] > last4[-1])

    # Opening-spike exhaustion definition:
    # - at least 3 of the last 4 one-minute MACD slopes are stepping down,
    # - regression slope of MACD slope is negative,
    # - and either the final 3 are strictly descending or current slope has faded
    #   at least 20% from the recent micro peak.
    # A later 1m turn-up naturally releases the WAIT state and allows re-entry.
    decay = bool(
        down_steps >= 2
        and trend < 0
        and (last3_down or (np.isfinite(fade_ratio) and fade_ratio <= 0.80))
    )
    return {
        'decay': decay, 'n': len(s), 'last3_down': last3_down,
        'down_steps': down_steps, 'trend': trend, 'peak': peak, 'last': last,
        'fade_ratio': fade_ratio, 'spread_last': float(sp[-1]) if len(sp) else np.nan,
        'rsi_last': float(rs[-1]) if len(rs) else np.nan, 'frame': q,
    }


def apply_v13(frames, raw, cfg):
    micros = {sym: build_1m_micro(raw[sym], cfg) for sym in frames}
    gaps = {sym: daily_gap_map(raw[sym]) for sym in frames}
    out = {}
    for sym, f0 in frames.items():
        f = f0.copy()
        waits = []
        decays = []
        for r in f.itertuples(index=False):
            ts = pd.Timestamp(r.time)
            minute = ts.hour * 60 + ts.minute
            gap = gaps[sym].get(ts.date(), np.nan)
            sensitive = bool(np.isfinite(gap) and gap >= GAP_PCT and OPEN_MINUTE <= minute < MICRO_END_MINUTE)
            st = micro_state_at(micros[sym], ts) if sensitive else {'decay': False}
            decays.append(bool(st['decay']))
            waits.append(bool(sensitive and st['decay']))
        f['macd_micro_decay_v13'] = decays
        f['opening_micro_wait_v13'] = waits
        f['entry_gate_v10_before_v13'] = f['entry_gate'].fillna(False)
        f['entry_gate_v13'] = f['entry_gate_v10_before_v13'] & ~f['opening_micro_wait_v13']
        f['entry_gate'] = f['entry_gate_v13']
        out[sym] = f
    return out, micros, gaps


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
    state_events = b.pack_state_events(b.build_cfg_frames(raw, base_cfg))

    raw_frames = b.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
    f13, micros, gaps = apply_v13(f10, raw, cfg)

    s10 = reweight(f10, cfg, 0.0)
    s13 = reweight(f13, cfg, 0.0)
    ev10 = filter_open(v8.pack_entry_events(s10))
    ev13 = filter_open(v8.pack_entry_events(s13))

    print('=== TARGET 1M MICRO TRACE: 484810 2026-08-11 09:05~09:09 ===')
    st = micro_state_at(micros[TARGET_SYMBOL], TARGET)
    q = st['frame']
    cols = ['time','close','macd_1m','signal_1m','macd_slope_1m','signal_slope_1m','spread_1m','rsi_1m','rsi_slope_1m']
    print(q[cols].round(6).to_string(index=False))
    print('\n=== TARGET MICRO DECAY STATE ===')
    print(f"gap={gaps[TARGET_SYMBOL].get(TARGET.date(), np.nan):.2f}% n={st['n']} down_steps={st['down_steps']} last3_down={st['last3_down']} trend={st['trend']:.6f} peak={st['peak']:.6f} last={st['last']:.6f} fade_ratio={st['fade_ratio']:.3f} decay={st['decay']}")

    def hit(ev):
        rows = ev.get(TARGET, [])
        h = [r for r in rows if str(r[0]).zfill(6) == TARGET_SYMBOL]
        return bool(h), h[0][2] if h else None

    h10, sc10 = hit(ev10)
    h13, sc13 = hit(ev13)
    print('\n=== TARGET REGRESSION ===')
    print(f'V10_candidate={h10} score={sc10}')
    print(f'V13_candidate={h13} score={sc13}')
    print('REGRESSION_PASS=', bool(h10 and st['decay'] and not h13))

    print('\n=== SINGLE-CONFIG REALIZED BACKTEST ===')
    t10 = run_case('B_V10_PLUS_0910', packed_exits, state_events, ev10)
    t13 = run_case('E_V13_1M_MACD_DECAY', packed_exits, state_events, ev13)

    k10 = set(zip(t10.symbol.astype(str).str.zfill(6), pd.to_datetime(t10.entry_time).astype(str)))
    k13 = set(zip(t13.symbol.astype(str).str.zfill(6), pd.to_datetime(t13.entry_time).astype(str)))
    print('\nREMOVED_REALIZED_FROM_B=', sorted(k10-k13))
    print('ADDED_REALIZED_VIA_PATH_CHANGE=', sorted(k13-k10))


if __name__ == '__main__':
    main()
