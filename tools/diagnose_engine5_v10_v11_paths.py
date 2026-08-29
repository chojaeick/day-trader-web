from __future__ import annotations

from dataclasses import replace

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as b
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_fast_tuner_v11 as v11
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def make_frames(raw, cfg):
    raw_frames = b.build_cfg_frames(raw, cfg)
    f10 = {sym: v10._refine_entry_frame(f) for sym, f in raw_frames.items()}
    f11 = {sym: v11._apply_gap_confirmation(f10[sym], v11._daily_gap_map(raw[sym])) for sym in f10}
    return f10, f11


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

    f10, f11 = make_frames(raw, cfg)
    s10 = reweight(f10, cfg, 0.0)
    s11 = reweight(f11, cfg, 0.0)

    ev10_all = v8.pack_entry_events(s10)
    ev10_open = filter_open(ev10_all)
    ev11_open = filter_open(v8.pack_entry_events(s11))

    print('=== EVENT COUNTS (candidate timestamps, not realized trades) ===')
    print('V10_all=', sum(len(x) for x in ev10_all.values()), 'timestamps=', len(ev10_all))
    print('V10_0910=', sum(len(x) for x in ev10_open.values()), 'timestamps=', len(ev10_open))
    print('V11_0910_gap=', sum(len(x) for x in ev11_open.values()), 'timestamps=', len(ev11_open))
    print('V11_subset_of_V10_0910=', set(ev11_open).issubset(set(ev10_open)))

    print('\n=== REALIZED SINGLE-POSITION BACKTEST ===')
    t_a = run_case('A_V10_ORIGINAL_NO_OPEN_BLOCK', packed_exits, state_events, ev10_all)
    t_b = run_case('B_V10_PLUS_0910_BLOCK', packed_exits, state_events, ev10_open)
    t_c = run_case('C_V11_PLUS_GAP_FILTER', packed_exits, state_events, ev11_open)

    target = pd.Timestamp('2026-08-11 09:10:00+09:00')
    print('\n=== 484810 2026-08-11 09:10 ===')
    for label, ev in [('A', ev10_all), ('B', ev10_open), ('C', ev11_open)]:
        rows = ev.get(target, [])
        hit = [r for r in rows if str(r[0]).zfill(6) == '484810']
        print(label, 'candidate=', bool(hit), 'score=', hit[0][2] if hit else None)

    print('\n=== PRE-09:10 REALIZED TRADES IN ORIGINAL V10 PATH ===')
    q = t_a[pd.to_datetime(t_a.entry_time).dt.hour * 60 + pd.to_datetime(t_a.entry_time).dt.minute < OPEN_MINUTE]
    if len(q):
        print(q[['symbol','entry_time','exit_time','pnl_pct','reason']].to_string(index=False))
    else:
        print('none')


if __name__ == '__main__':
    main()
