from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, load_data, simulate_legacy, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached, simulate_v22_adaptive

OUT = Path('/home/ubuntu/day-trader-api')
THRESHOLDS = [55, 60, 65, 70, 75, 80]


@dataclass
class Pos:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    entry_score: float
    remaining_fraction: float = 1.0
    realized_pct: float = 0.0
    partial_done: bool = False
    outer_broken: bool = False


def to_5m(bars: pd.DataFrame) -> pd.DataFrame:
    x = bars.copy().sort_values('time')
    x['time'] = pd.to_datetime(x['time'])
    x['bucket'] = x['time'].dt.floor('5min')
    g = x.groupby('bucket', sort=True)
    z = g.agg(open=('open','first'), high=('high','max'), low=('low','min'), close=('close','last'), volume=('volume','sum'), rows=('close','size')).reset_index()
    z = z[z.rows >= 5].copy()
    z['time'] = z['bucket'] + pd.Timedelta(minutes=5)
    return z[['time','open','high','low','close','volume']].reset_index(drop=True)


def build_frames(raw):
    eng = DoubleBollingerEngine5()
    out = {}
    for sym, bars in sorted(raw.items()):
        f = eng.enrich(to_5m(bars))
        f['symbol'] = sym
        out[sym] = f
        print(f"[E5 5M] {sym} bars={len(f)} score>=55={int((f.entry_score>=55).sum())} score>=70={int((f.entry_score>=70).sum())}", flush=True)
    return out


def events(frames):
    e = {}
    for sym, f in frames.items():
        for _, r in f.iterrows():
            e.setdefault(pd.Timestamp(r.time), []).append((sym, r))
    return e


def close_trade(pos, price, ts, reason):
    pnl = pos.realized_pct + pos.remaining_fraction * (float(price) / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol, 'entry_time': pos.entry_time, 'exit_time': pd.Timestamp(ts),
        'entry_price': pos.entry_price, 'exit_price': float(price), 'entry_score': pos.entry_score,
        'pnl_pct': pnl * 100.0, 'partial_done': pos.partial_done, 'reason': reason,
    }


def simulate(frames, threshold):
    ev = events(frames)
    pos = None
    trades = []
    collisions = 0
    for ts in sorted(ev):
        minute = ts.hour * 60 + ts.minute
        rows = ev[ts]

        if pos is not None:
            r = next((x for s, x in rows if s == pos.symbol), None)
            if r is not None:
                p, hi, lo = float(r.close), float(r.high), float(r.low)
                ou = float(r.outer_upper) if pd.notna(r.outer_upper) else np.nan
                il = float(r.inner_lower) if pd.notna(r.inner_lower) else np.nan
                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, p, ts, 'SESSION_FORCE_FLAT'))
                    pos = None
                    continue
                if not pos.partial_done and np.isfinite(ou):
                    if hi >= ou:
                        pos.outer_broken = True
                    if pos.outer_broken and p < ou:
                        pos.realized_pct += 0.5 * (p / pos.entry_price - 1.0)
                        pos.remaining_fraction = 0.5
                        pos.partial_done = True
                if float(r.macd_slope) < 0 and float(r.rsi_slope) < 0 and np.isfinite(il) and lo <= il:
                    trades.append(close_trade(pos, p, ts, 'E5_FULL_EXIT_MACD_RSI_INNER_LOWER'))
                    pos = None
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            c = [(s, r) for s, r in rows if pd.notna(r.entry_score) and float(r.entry_score) >= float(threshold)]
            if c:
                if len(c) > 1:
                    collisions += 1
                # When several symbols qualify simultaneously, choose the strongest score only.
                sym, r = max(c, key=lambda z: (float(z[1].entry_score), float(z[1].volume_ratio or 0.0), z[0]))
                pos = Pos(sym, pd.Timestamp(ts), float(r.close), float(r.entry_score))

    if pos is not None:
        r = frames[pos.symbol].iloc[-1]
        trades.append(close_trade(pos, float(r.close), r.time, 'END_OF_DATA'))
    t = pd.DataFrame(trades)
    return t, collisions


def metrics(name, t):
    r = summary(name, t)
    r['median_pct'] = round(float(t.pnl_pct.median()), 4) if not t.empty else 0.0
    return r


def main():
    raw = load_data()
    print(f"[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}")

    frames1 = build_frames_cached(raw, workers=2, rebuild=False)
    e1 = simulate_legacy(frames1, 'base_entry')
    e2 = simulate_legacy(frames1, 'structure_entry')
    e3 = simulate_v22_adaptive(frames1, 'structure_entry')
    frames5 = build_frames(raw)

    rows = [metrics('ENGINE_1_V2_BASE_1M', e1), metrics('ENGINE_2_V21_STRUCTURE_1M', e2), metrics('ENGINE_3_V22_ADAPTIVE_1M', e3)]
    all_e5 = []
    for th in THRESHOLDS:
        t, collisions = simulate(frames5, th)
        r = metrics(f'ENGINE_5_SCORE_{th}', t)
        r['threshold'] = th
        r['collisions'] = collisions
        rows.append(r)
        all_e5.append(t.assign(threshold=th))
        print(f"[E5 SCORE {th}] trades={len(t)} win={(t.pnl_pct>0).mean()*100 if len(t) else 0:.2f}% gross={t.pnl_pct.sum() if len(t) else 0:+.4f}% collisions={collisions}")

    board = pd.DataFrame(rows)
    cols = ['version','threshold','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','partial_rate','median_pct','collisions']
    print('\n=== ENGINES 1 / 2 / 3 + ENGINE 5 SCORE SWEEP ===')
    print(board[[c for c in cols if c in board.columns]].to_string(index=False))

    e5all = pd.concat(all_e5, ignore_index=True) if all_e5 else pd.DataFrame()
    board.to_csv(OUT / 'dbb_engine5_score_sweep_summary.csv', index=False)
    e5all.to_csv(OUT / 'dbb_engine5_score_sweep_trades.csv', index=False)

    print('\n[ENGINE 5 INITIAL SCORE WEIGHTS]')
    print('trend20 + MACD_state15 + MACD_gap_widen10 + golden5 + RSI_rising15 + RSI_accel10 + volume10 + outer_expand10 + inner_traverse5 = 100')
    print('volume/outer expansion are graded strength scores, not hard filters; Bollinger position is not a gate')
    print('threshold sweep = 55,60,65,70,75,80')
    print('[CSV] dbb_engine5_score_sweep_summary.csv, dbb_engine5_score_sweep_trades.csv')


if __name__ == '__main__':
    main()
