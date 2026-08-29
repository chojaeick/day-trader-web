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
TARGET = ('484810', pd.Timestamp('2026-08-11 09:10:00+09:00'))


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


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


def micro_features(micro: pd.DataFrame, ts: pd.Timestamp) -> dict:
    t = pd.Timestamp(ts)
    q = micro[(micro.time >= t - pd.Timedelta(minutes=5)) & (micro.time < t)].copy()
    s = pd.to_numeric(q['macd_slope_1m'], errors='coerce').dropna().to_numpy(float)
    sp = pd.to_numeric(q['spread_1m'], errors='coerce').dropna().to_numpy(float)
    rs = pd.to_numeric(q['rsi_slope_1m'], errors='coerce').dropna().to_numpy(float)
    close = pd.to_numeric(q['close'], errors='coerce').dropna().to_numpy(float)
    if len(s) < 4:
        return {'n':len(s)}
    last4 = s[-4:]
    diffs = np.diff(last4)
    down_steps = int((diffs < 0).sum())
    x = np.arange(len(last4), dtype=float)
    trend = float(np.polyfit(x, last4, 1)[0])
    peak = float(np.max(last4))
    last = float(last4[-1])
    fade_ratio = last / peak if peak > 0 else np.nan
    last3_down = bool(last4[-3] > last4[-2] > last4[-1])
    prev_last = float(last4[-2])
    step_ratio = last / prev_last if prev_last > 0 else np.nan
    peak_drop_pct = (1.0 - fade_ratio) * 100.0 if np.isfinite(fade_ratio) else np.nan
    spread_last = float(sp[-1]) if len(sp) else np.nan
    spread_change = float(sp[-1] - sp[0]) if len(sp) >= 2 else np.nan
    rsi_last = float(rs[-1]) if len(rs) else np.nan
    price_pullback_pct = (float(close[-1]) / float(np.max(close)) - 1.0) * 100.0 if len(close) else np.nan
    return {
        'n': len(s), 'down_steps': down_steps, 'trend': trend, 'peak': peak, 'last': last,
        'fade_ratio': fade_ratio, 'peak_drop_pct': peak_drop_pct, 'step_ratio': step_ratio,
        'spread_last': spread_last, 'spread_change': spread_change, 'rsi_last': rsi_last,
        'price_pullback_pct': price_pullback_pct, 'last3_down': last3_down,
    }


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
    s10 = reweight(f10, cfg, 0.0)
    ev10 = filter_open(v8.pack_entry_events(s10))
    t10 = run_case('B_V10_PLUS_0910', packed_exits, state_events, ev10)

    micros = {sym: build_1m_micro(raw[sym], cfg) for sym in raw}
    gaps = {sym: daily_gap_map(raw[sym]) for sym in raw}

    rows = []
    for r in t10.itertuples(index=False):
        sym = str(r.symbol).zfill(6)
        ts = pd.Timestamp(r.entry_time)
        minute = ts.hour * 60 + ts.minute
        gap = gaps[sym].get(ts.date(), np.nan)
        if not (np.isfinite(gap) and gap >= GAP_PCT and OPEN_MINUTE <= minute < MICRO_END_MINUTE):
            continue
        ft = micro_features(micros[sym], ts)
        rows.append({'symbol':sym,'entry_time':ts,'pnl_pct':float(r.pnl_pct),'gap_pct':gap,**ft})
    q = pd.DataFrame(rows)
    print('\n=== GAP>=4% PRE-10:00 REALIZED MICRO FEATURES ===')
    print(q.sort_values('pnl_pct').round(4).to_string(index=False))

    # Find a simple boundary on slope decay that blocks the target but preserves all winners in this observed set.
    target = q[(q.symbol == TARGET[0]) & (q.entry_time == TARGET[1])].iloc[0]
    candidates = []
    for fade_cut in np.arange(0.20, 0.61, 0.025):
        for step_cut in np.arange(0.20, 0.81, 0.05):
            for down_min in [2,3]:
                for trend_req in [False, True]:
                    block = (q.fade_ratio <= fade_cut) & (q.step_ratio <= step_cut) & (q.down_steps >= down_min)
                    if trend_req:
                        block &= q.trend < 0
                    # target must be blocked
                    tb = bool(((q.symbol == TARGET[0]) & (q.entry_time == TARGET[1]) & block).any())
                    if not tb:
                        continue
                    bw = q[block & (q.pnl_pct > 0)]
                    bl = q[block & (q.pnl_pct <= 0)]
                    candidates.append({
                        'fade_cut':fade_cut,'step_cut':step_cut,'down_min':down_min,'trend_req':trend_req,
                        'blocked':int(block.sum()),'wins_blocked':len(bw),'losses_blocked':len(bl),
                        'blocked_pnl':float(q.loc[block,'pnl_pct'].sum()),
                    })
    c = pd.DataFrame(candidates)
    if len(c):
        c = c.sort_values(['wins_blocked','losses_blocked','blocked_pnl','blocked'], ascending=[True,False,True,True])
        print('\n=== BEST SLOPE-DECAY BOUNDARIES ===')
        print(c.head(20).round(4).to_string(index=False))
        best = c.iloc[0]
        block = (q.fade_ratio <= best.fade_cut) & (q.step_ratio <= best.step_cut) & (q.down_steps >= int(best.down_min))
        if bool(best.trend_req):
            block &= q.trend < 0
        print('\n=== BEST RULE BLOCKED TRADES ===')
        print(q[block].sort_values('pnl_pct').round(4).to_string(index=False))
        print('\n=== BEST RULE SURVIVING WINNERS ===')
        print(q[(~block) & (q.pnl_pct > 0)].sort_values('entry_time').round(4).to_string(index=False))
        print('\nRULE=', f"fade_ratio<={best.fade_cut:.3f} AND step_ratio<={best.step_cut:.3f} AND down_steps>={int(best.down_min)}" + (' AND trend<0' if bool(best.trend_req) else ''))
    else:
        print('No separating slope boundary found.')


if __name__ == '__main__':
    main()
