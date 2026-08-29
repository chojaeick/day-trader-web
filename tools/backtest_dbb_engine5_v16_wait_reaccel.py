from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v15_boundary as v15
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
MICRO_END_MINUTE = 10 * 60
TARGET = pd.Timestamp('2026-08-11 09:10:00+09:00')
TARGET_SYMBOL = '484810'


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def build_rich_micro(raw_bars: pd.DataFrame, cfg: DoubleBollingerEngine5Config) -> pd.DataFrame:
    f = raw_bars.copy().sort_values('time').reset_index(drop=True)
    f['time'] = pd.to_datetime(f['time'])
    close = pd.to_numeric(f['close'], errors='coerce').astype(float)
    eng = DoubleBollingerEngine5(cfg)
    macd, signal = eng._macd(close)
    rsi = eng._rsi(close, cfg.rsi_period)
    f['macd_1m'] = macd
    f['signal_1m'] = signal
    f['macd_slope_1m'] = macd.diff()
    f['signal_slope_1m'] = signal.diff()
    f['spread_1m'] = f['macd_slope_1m'] - f['signal_slope_1m']
    f['rsi_1m'] = rsi
    f['rsi_slope_1m'] = rsi.diff()
    return f[['time','open','high','low','close','macd_slope_1m','spread_1m','rsi_slope_1m']]


def is_reaccel(prev_row, row) -> bool:
    vals = [prev_row.macd_slope_1m, row.macd_slope_1m, prev_row.spread_1m, row.spread_1m, row.rsi_slope_1m]
    if not all(np.isfinite(float(x)) for x in vals):
        return False
    return bool(
        float(row.macd_slope_1m) > 0
        and float(row.macd_slope_1m) > float(prev_row.macd_slope_1m)
        and float(row.spread_1m) > float(prev_row.spread_1m)
        and float(row.rsi_slope_1m) > 0
    )


def candidate_is_v15_wait(sym: str, ts: pd.Timestamp, raw, micros_v15, gaps) -> tuple[bool, dict]:
    t = pd.Timestamp(ts)
    minute = t.hour * 60 + t.minute
    gap = gaps[sym].get(t.date(), np.nan)
    sensitive = bool(np.isfinite(gap) and gap >= v15.GAP_PCT and OPEN_MINUTE <= minute < MICRO_END_MINUTE)
    st = v15.slope_state_at(micros_v15[sym], t) if sensitive else {'block': False, 'down_steps': 0, 'fade_ratio': np.nan, 'step_ratio': np.nan}
    return bool(sensitive and st['block']), {'gap': gap, **st}


def delayed_event_from(original_event, delayed_ts, delayed_close):
    # Preserve the completed 5m signal's DBB geometry for its natural 5-minute
    # lifetime; only execution time/price changes. This avoids peeking at an
    # uncompleted next 5m bar.
    e = list(original_event)
    e[1] = float(delayed_close)
    return tuple(e)


def build_wait_events(ev10, raw, cfg, require_better_price: bool):
    rich = {sym: build_rich_micro(raw[sym], cfg) for sym in raw}
    micro15 = {sym: v15.build_1m_micro(raw[sym], cfg) for sym in raw}
    gaps = {sym: v15.daily_gap_map(raw[sym]) for sym in raw}

    out = {ts: list(rows) for ts, rows in ev10.items()}
    waits = []

    # Each blocked 5m signal becomes WAIT for the remainder of that signal's
    # 5-minute lifetime. A new 5m signal is evaluated independently; no cooldown.
    for ts in sorted(ev10):
        t = pd.Timestamp(ts)
        for event in list(ev10[ts]):
            sym = str(event[0]).zfill(6)
            wait, st = candidate_is_v15_wait(sym, t, raw, micro15, gaps)
            if not wait:
                continue

            # Remove immediate BUY.
            out[ts] = [x for x in out.get(ts, []) if not (str(x[0]).zfill(6) == sym and x == event)]
            if not out[ts]:
                out.pop(ts, None)

            m = rich[sym]
            q = m[(m['time'] >= t) & (m['time'] < t + pd.Timedelta(minutes=5))].copy()
            q = q[q['time'].dt.hour * 60 + q['time'].dt.minute < MICRO_END_MINUTE]
            original_price = float(event[1])
            chosen = None
            prev = None
            for row in q.itertuples(index=False):
                if prev is not None and is_reaccel(prev, row):
                    better = float(row.close) <= original_price
                    if (not require_better_price) or better:
                        chosen = row
                        break
                prev = row

            record = {
                'symbol': sym, 'signal_time': t, 'signal_price': original_price,
                'gap_pct': st['gap'], 'down_steps': st['down_steps'],
                'fade_ratio': st['fade_ratio'], 'step_ratio': st['step_ratio'],
                'delayed_time': pd.NaT, 'delayed_price': np.nan,
                'price_improvement_pct': np.nan, 'status': 'NO_REACCEL',
            }
            if chosen is not None:
                dts = pd.Timestamp(chosen.time)
                dpx = float(chosen.close)
                out.setdefault(dts, []).append(delayed_event_from(event, dts, dpx))
                record.update({
                    'delayed_time': dts, 'delayed_price': dpx,
                    'price_improvement_pct': (original_price / dpx - 1.0) * 100.0,
                    'status': 'REACCEL_ENTRY',
                })
            waits.append(record)

    return out, pd.DataFrame(waits)


def run_case(name, packed_exits, state_events, events):
    t, collisions = v8.v7.simulate_v7(packed_exits, events, state_events, THRESHOLD)
    s = summary(name, t)
    print(f"{name}: trades={len(t)} wins={(t.pnl_pct>0).sum()} losses={(t.pnl_pct<=0).sum()} win={s['win_rate']:.2f} avg={s['avg_pct']:.4f} gross={s['gross_pct']:.4f} pf={s['pf']:.3f} collisions={collisions}")
    return t


def trade_diff(a, b):
    ka = set(zip(a.symbol.astype(str).str.zfill(6), pd.to_datetime(a.entry_time).astype(str)))
    kb = set(zip(b.symbol.astype(str).str.zfill(6), pd.to_datetime(b.entry_time).astype(str)))
    return sorted(ka-kb), sorted(kb-ka)


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))

    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filter_open(v8.pack_entry_events(scored))

    ev_wait, waits = build_wait_events(ev10, raw, cfg, require_better_price=False)
    ev_better, waits_better = build_wait_events(ev10, raw, cfg, require_better_price=True)

    print('=== V16 IDEA ===')
    print('V15 severe slope decay no longer means permanent BLOCK. It becomes WAIT for the remaining lifetime of that 5m signal.')
    print('REACCEL = 1m MACD slope turns up while positive + MACD-vs-signal spread improves + RSI slope > 0.')
    print('No fixed cooldown. If no reacceleration before the next completed 5m signal, that old signal expires.')

    print('\n=== WAIT / DELAYED ENTRY MAP: REACCEL ===')
    print(waits.to_string(index=False) if len(waits) else 'none')
    print('\n=== WAIT / DELAYED ENTRY MAP: REACCEL + BETTER PRICE ===')
    print(waits_better.to_string(index=False) if len(waits_better) else 'none')

    print('\n=== SINGLE-CONFIG REALIZED BACKTEST ===')
    t10 = run_case('B_V10_PLUS_0910', packed_exits, state_events, ev10)
    tv15_frames, _ = v15.apply_v15(f10, raw, cfg)
    tv15_scored = reweight(tv15_frames, cfg, 0.0)
    ev15 = filter_open(v8.pack_entry_events(tv15_scored))
    t15 = run_case('F_V15_BLOCK', packed_exits, state_events, ev15)
    tw = run_case('G_V16_WAIT_REACCEL', packed_exits, state_events, ev_wait)
    tb = run_case('H_V16_WAIT_REACCEL_BETTER_PRICE', packed_exits, state_events, ev_better)

    print('\n=== PATH CHANGES VS V10 ===')
    for label, t in [('V15', t15), ('V16_REACCEL', tw), ('V16_BETTER', tb)]:
        removed, added = trade_diff(t10, t)
        print(label, 'REMOVED=', removed)
        print(label, 'ADDED=', added)

    print('\n=== TARGET 484810 2026-08-11 09:10 ===')
    q = waits[(waits.symbol == TARGET_SYMBOL) & (pd.to_datetime(waits.signal_time) == TARGET)] if len(waits) else waits
    if len(q):
        print(q.to_string(index=False))
    else:
        print('target_wait_record=NONE')
    target_rows = []
    for ts, rows in ev_wait.items():
        if ts >= TARGET and ts < TARGET + pd.Timedelta(minutes=5):
            for e in rows:
                if str(e[0]).zfill(6) == TARGET_SYMBOL:
                    target_rows.append((ts, float(e[1])))
    print('TARGET_DELAYED_ENTRIES=', target_rows)


if __name__ == '__main__':
    main()
