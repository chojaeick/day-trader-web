from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as b
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.diagnose_engine5_v13_open_micro_decay as v13
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

THRESHOLD = 50
OPEN_MINUTE = 9 * 60 + 10
MICRO_END_MINUTE = 10 * 60
GAP_PCT = 4.0
TARGET_SYMBOL = '484810'
TARGET = pd.Timestamp('2026-08-11 09:10:00+09:00')


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def micro_features(micro: pd.DataFrame, ts: pd.Timestamp) -> dict:
    t = pd.Timestamp(ts)
    q = micro[(micro['time'] >= t - pd.Timedelta(minutes=5)) & (micro['time'] < t)].copy()
    s = pd.to_numeric(q['macd_slope_1m'], errors='coerce').dropna().to_numpy(float)
    sp = pd.to_numeric(q['spread_1m'], errors='coerce').dropna().to_numpy(float)
    rs = pd.to_numeric(q['rsi_slope_1m'], errors='coerce').dropna().to_numpy(float)
    closes = pd.to_numeric(q['close'], errors='coerce').dropna().to_numpy(float)
    if len(s) < 4:
        return {'n': len(s), 'down_steps': 0, 'trend': np.nan, 'fade_ratio': np.nan,
                'spread_last': np.nan, 'spread_change': np.nan, 'rsi_last': np.nan,
                'price_pullback_pct': np.nan, 'last3_down': False}
    last4 = s[-4:]
    diffs = np.diff(last4)
    peak = float(np.max(last4))
    last = float(last4[-1])
    x = np.arange(len(last4), dtype=float)
    trend = float(np.polyfit(x, last4, 1)[0])
    spread_last = float(sp[-1]) if len(sp) else np.nan
    spread_change = float(sp[-1] - sp[0]) if len(sp) >= 2 else np.nan
    rsi_last = float(rs[-1]) if len(rs) else np.nan
    price_pullback_pct = (float(closes[-1]) / float(np.max(closes)) - 1.0) * 100.0 if len(closes) else np.nan
    return {
        'n': len(s),
        'down_steps': int((diffs < 0).sum()),
        'trend': trend,
        'fade_ratio': last / peak if peak > 0 else np.nan,
        'spread_last': spread_last,
        'spread_change': spread_change,
        'rsi_last': rsi_last,
        'price_pullback_pct': price_pullback_pct,
        'last3_down': bool(last4[-3] > last4[-2] > last4[-1]),
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
    scored = reweight(f10, cfg, 0.0)
    ev10 = filter_open(v8.pack_entry_events(scored))
    t10 = run_case('B_V10_PLUS_0910', packed_exits, state_events, ev10)

    micros = {sym: v13.build_1m_micro(raw[sym], cfg) for sym in raw}
    gaps = {sym: v13.daily_gap_map(raw[sym]) for sym in raw}

    rows = []
    for _, tr in t10.iterrows():
        sym = str(tr['symbol']).zfill(6)
        ts = pd.Timestamp(tr['entry_time'])
        minute = ts.hour * 60 + ts.minute
        gap = gaps[sym].get(ts.date(), np.nan)
        if not (np.isfinite(gap) and gap >= GAP_PCT and OPEN_MINUTE <= minute < MICRO_END_MINUTE):
            continue
        ft = micro_features(micros[sym], ts)
        rows.append({
            'symbol': sym, 'entry_time': ts, 'pnl_pct': float(tr['pnl_pct']), 'gap_pct': gap,
            **ft,
        })

    d = pd.DataFrame(rows)
    print('\n=== V10 REALIZED GAP>=4% PRE-10:00 MICRO FEATURES ===')
    if d.empty:
        print('none')
        return
    cols = ['symbol','entry_time','pnl_pct','gap_pct','down_steps','trend','fade_ratio','spread_last','spread_change','rsi_last','price_pullback_pct','last3_down']
    print(d[cols].sort_values(['pnl_pct']).round(4).to_string(index=False))

    print('\n=== WIN vs LOSS SUMMARY ===')
    d['outcome'] = np.where(d['pnl_pct'] > 0, 'WIN', 'LOSS')
    print(d.groupby('outcome')[['down_steps','trend','fade_ratio','spread_last','spread_change','rsi_last','price_pullback_pct']].agg(['count','mean','median','min','max']).round(4).to_string())

    # Boundary search: require MACD slope decay plus one or more deterioration confirmations.
    # We search only transparent thresholds, and rank rules that remove losses while preserving wins.
    candidates = []
    for fade in [0.20,0.25,0.30,0.35,0.40,0.50,0.60,0.70,0.80]:
        for ds in [2,3]:
            for spread_mode in ['NONE','NEG','DROP20','DROP40','DROP60']:
                for rsi_mode in ['NONE','NEG']:
                    for pull in [None,-0.25,-0.50,-0.75,-1.00]:
                        base_decay = (d['down_steps'] >= ds) & (d['trend'] < 0) & (d['fade_ratio'] <= fade)
                        confirm = pd.Series(True, index=d.index)
                        if spread_mode == 'NEG':
                            confirm &= d['spread_last'] < 0
                        elif spread_mode == 'DROP20':
                            confirm &= d['spread_change'] <= -20
                        elif spread_mode == 'DROP40':
                            confirm &= d['spread_change'] <= -40
                        elif spread_mode == 'DROP60':
                            confirm &= d['spread_change'] <= -60
                        if rsi_mode == 'NEG':
                            confirm &= d['rsi_last'] < 0
                        if pull is not None:
                            confirm &= d['price_pullback_pct'] <= pull
                        block = base_decay & confirm
                        target_hit = bool(block[(d['symbol']==TARGET_SYMBOL) & (pd.to_datetime(d['entry_time'])==TARGET)].any())
                        if not target_hit:
                            continue
                        wins_blocked = int(((d['pnl_pct'] > 0) & block).sum())
                        losses_blocked = int(((d['pnl_pct'] <= 0) & block).sum())
                        blocked_pnl = float(d.loc[block,'pnl_pct'].sum())
                        # rank: preserve wins first, then remove more losing PnL, then losses count
                        candidates.append((wins_blocked, blocked_pnl, -losses_blocked, fade, ds, spread_mode, rsi_mode, pull, int(block.sum())))

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    print('\n=== BEST TRANSPARENT BOUNDARIES (target must be blocked) ===')
    for c in candidates[:20]:
        wins_blocked, blocked_pnl, neg_losses, fade, ds, sm, rm, pull, n = c
        print(f'fade<={fade:.2f} down_steps>={ds} spread={sm} rsi={rm} pull<={pull} | blocked={n} wins_blocked={wins_blocked} losses_blocked={-neg_losses} blocked_pnl_sum={blocked_pnl:.4f}')

    # Show exact trades blocked by the best rule.
    if candidates:
        c = candidates[0]
        _, _, _, fade, ds, sm, rm, pull, _ = c
        block = (d['down_steps'] >= ds) & (d['trend'] < 0) & (d['fade_ratio'] <= fade)
        if sm == 'NEG': block &= d['spread_last'] < 0
        elif sm == 'DROP20': block &= d['spread_change'] <= -20
        elif sm == 'DROP40': block &= d['spread_change'] <= -40
        elif sm == 'DROP60': block &= d['spread_change'] <= -60
        if rm == 'NEG': block &= d['rsi_last'] < 0
        if pull is not None: block &= d['price_pullback_pct'] <= pull
        print('\n=== BEST RULE BLOCKED TRADES ===')
        print(d.loc[block, cols].sort_values('pnl_pct').round(4).to_string(index=False))


if __name__ == '__main__':
    main()
