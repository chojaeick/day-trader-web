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
HORIZONS = (1, 3, 5, 10, 20)


def filt_open(ev):
    return {
        ts: rows
        for ts, rows in ev.items()
        if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE
    }


def build_minute_index(packed_exits):
    out = {}
    for ts, minute, rows in packed_exits:
        pts = pd.Timestamp(ts)
        for sym, rr in rows.items():
            out.setdefault(str(sym).zfill(6), []).append((pts, rr))
    return out


def path_metrics(minute_index, sym: str, entry_time: pd.Timestamp, entry_price: float) -> dict:
    rows = minute_index.get(str(sym).zfill(6), [])
    future = [(ts, rr) for ts, rr in rows if ts > entry_time and ts <= entry_time + pd.Timedelta(minutes=max(HORIZONS))]
    out = {}
    for h in HORIZONS:
        w = [(ts, rr) for ts, rr in future if ts <= entry_time + pd.Timedelta(minutes=h)]
        if not w:
            out[f'ret_{h}m_pct'] = np.nan
            out[f'mfe_{h}m_pct'] = np.nan
            out[f'mae_{h}m_pct'] = np.nan
            continue
        closes = [float(rr[0]) for _, rr in w]
        lows = [float(rr[1]) for _, rr in w]
        highs = [float(rr[2]) for _, rr in w]
        out[f'ret_{h}m_pct'] = (closes[-1] / entry_price - 1.0) * 100.0
        out[f'mfe_{h}m_pct'] = (max(highs) / entry_price - 1.0) * 100.0
        out[f'mae_{h}m_pct'] = (min(lows) / entry_price - 1.0) * 100.0
    return out


def nearest_v16_trade(t16: pd.DataFrame, sym: str, ts: pd.Timestamp):
    if t16.empty:
        return None
    q = t16.copy()
    q['entry_time'] = pd.to_datetime(q['entry_time'])
    q = q[(q.symbol.astype(str).str.zfill(6) == str(sym).zfill(6)) & (q.entry_time.dt.date == ts.date()) & (q.entry_time >= ts)]
    if q.empty:
        return None
    return q.sort_values('entry_time').iloc[0]


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed_exits = v8.base.pack_exit_events(raw, base_cfg)
    state_events = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    minute_index = build_minute_index(packed_exits)

    raw_frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in raw_frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev16x = {ts: [tuple(list(e) + [False]) for e in rows] for ts, rows in ev16.items()}
    ev17b, added, skipped = v17b.build_v17b(ev16, scored, waits)

    # Full V16 path is used only as a reference for "what the current single-position engine did later".
    t16, s16 = v17.run('V16_REFERENCE', packed_exits, state_events, ev16x)

    records = []
    print('\n=== V17B INDEPENDENT BREAKOUT CANDIDATE VALIDATION ===')
    print('Each >=10x breakout candidate is simulated independently, so another open position cannot hide its outcome.')
    print('V16 WAIT remains a hard veto. First 10m uses the existing tight breakout protection: 1R hard stop + HWM -1% exit only after momentum cooling; then normal Engine5 exit logic.')
    print('\nV16_WAIT_VETOED_BREAKOUTS=', skipped)
    print('BREAKOUT_CANDIDATES=', added)

    for sym, ts, price, vol_ratio in added:
        ts = pd.Timestamp(ts)
        candidates = [e for e in ev17b.get(ts, []) if str(e[0]).zfill(6) == str(sym).zfill(6) and bool(e[-1])]
        if not candidates:
            print(f'WARN missing event tuple for {sym} {ts}')
            continue
        e = candidates[0]
        one_events = {ts: [e]}
        t, c = v17.simulate_v17(packed_exits, one_events, state_events, THRESHOLD)
        row = t.iloc[0] if len(t) else None
        pm = path_metrics(minute_index, sym, ts, float(price))
        ref = nearest_v16_trade(t16, sym, ts)

        rec = {
            'symbol': str(sym).zfill(6),
            'candidate_time': ts,
            'candidate_price': float(price),
            'volume_ratio_prev5m': float(vol_ratio),
            **pm,
            'independent_realized': bool(row is not None),
            'independent_exit_time': pd.Timestamp(row.exit_time) if row is not None else pd.NaT,
            'independent_exit_price': float(row.exit_price) if row is not None else np.nan,
            'independent_pnl_pct': float(row.pnl_pct) if row is not None else np.nan,
            'independent_reason': str(row.reason) if row is not None else 'NO_TRADE',
            'independent_first_tp': bool(row.first_tp_done) if row is not None else False,
            'independent_second_tp': bool(row.second_tp_done) if row is not None else False,
            'v16_next_same_symbol_entry_time': pd.Timestamp(ref.entry_time) if ref is not None else pd.NaT,
            'v16_next_same_symbol_entry_price': float(ref.entry_price) if ref is not None else np.nan,
            'v16_next_same_symbol_pnl_pct': float(ref.pnl_pct) if ref is not None else np.nan,
            'minutes_earlier_than_v16': ((pd.Timestamp(ref.entry_time) - ts).total_seconds() / 60.0) if ref is not None else np.nan,
        }
        records.append(rec)

        print('\n---', sym, ts, '---')
        print(f'entry={price:.2f}  volume_ratio={vol_ratio:.3f}x')
        for h in HORIZONS:
            print(f'{h:>2}m: ret={pm[f"ret_{h}m_pct"]:+.3f}%  MFE={pm[f"mfe_{h}m_pct"]:+.3f}%  MAE={pm[f"mae_{h}m_pct"]:+.3f}%')
        if row is None:
            print('independent_trade=NONE')
        else:
            print(f'independent_exit={pd.Timestamp(row.exit_time)} pnl={float(row.pnl_pct):+.4f}% reason={row.reason}')
        if ref is None:
            print('V16_next_same_symbol_trade=NONE')
        else:
            mins = (pd.Timestamp(ref.entry_time) - ts).total_seconds() / 60.0
            print(f'V16_next_same_symbol={pd.Timestamp(ref.entry_time)} price={float(ref.entry_price):.2f} pnl={float(ref.pnl_pct):+.4f}% delay={mins:.0f}m')

    df = pd.DataFrame(records)
    print('\n=== INDEPENDENT SUMMARY ===')
    if df.empty:
        print('No non-vetoed breakout candidates.')
    else:
        pnl = pd.to_numeric(df['independent_pnl_pct'], errors='coerce').dropna()
        print(f'candidates={len(df)} realized={len(pnl)} wins={(pnl > 0).sum()} losses={(pnl <= 0).sum()} win_rate={((pnl > 0).mean()*100.0 if len(pnl) else 0.0):.2f}% gross={pnl.sum():+.4f}% avg={pnl.mean():+.4f}%')
        print('\nRESULT TABLE')
        cols = ['symbol','candidate_time','candidate_price','volume_ratio_prev5m','ret_5m_pct','mfe_10m_pct','mae_10m_pct','ret_20m_pct','independent_pnl_pct','independent_reason','v16_next_same_symbol_entry_time','v16_next_same_symbol_pnl_pct','minutes_earlier_than_v16']
        print(df[cols].to_string(index=False))

    out = OUTDIR / 'v17b_breakout_independent_validation.csv'
    df.to_csv(out, index=False)
    print('\n[CSV]', out)


if __name__ == '__main__':
    main()
