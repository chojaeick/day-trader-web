from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerV2Config

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
SOURCE = 'kiwoom_ka10080'
MIN_BARS = 40
NO_ENTRY_MINUTE = 15 * 60
FORCE_FLAT_MINUTE = 15 * 60 + 20
FALLBACK_RISK_PCT = 0.012
PROFIT_ARM_PCT = 0.004
PARTIAL_MIN_GAIN_PCT = 0.005
PRE_PARTIAL_PULLBACK_PCT = 0.0035
RUNNER_TRAIL_STRONG_PCT = 0.008
RUNNER_TRAIL_NORMAL_PCT = 0.005
MOMENTUM_FAIL_BARS = 2


@dataclass
class Pos:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    score: float
    stage: str
    regime: str
    high_watermark: float
    partial_done: bool = False
    momentum_fail_count: int = 0
    realized_pct: float = 0.0
    remaining_fraction: float = 1.0


def load_data():
    con = sqlite3.connect(DB)
    q = """select symbol, ts, open, high, low, close, volume from historical_minute_bars where source=? and interval_min=1 order by ts,symbol"""
    df = pd.read_sql_query(q, con, params=(SOURCE,))
    con.close()
    df['time'] = pd.to_datetime(df['ts'], errors='coerce')
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['time','open','high','low','close']).copy()
    return {s:g[['time','open','high','low','close','volume']].sort_values('time').reset_index(drop=True) for s,g in df.groupby('symbol')}


def enrich(symbol, bars):
    engine = DoubleBollingerV2(DoubleBollingerV2Config(open_bonus_score=0.0))
    engine.ALLOWED_SYMBOLS = {symbol}
    rows = []
    for i in range(MIN_BARS - 1, len(bars)):
        window = bars.iloc[max(0, i - 120):i + 1]
        d = engine.entry_diagnostics(symbol, window)
        if not d.get('ready'):
            continue
        x = bars.iloc[i]
        b = engine._bands(pd.to_numeric(window['close'], errors='coerce').astype(float))
        mid = float(b['mid'].iloc[-1])
        mid3 = float(b['mid'].iloc[-4]) if len(b['mid']) >= 4 and pd.notna(b['mid'].iloc[-4]) else mid
        iu = float(b['inner_upper'].iloc[-1])
        iu3 = float(b['inner_upper'].iloc[-4]) if len(b['inner_upper']) >= 4 and pd.notna(b['inner_upper'].iloc[-4]) else iu
        mid_slope3 = (mid - mid3) / max(abs(mid3), 1e-9)
        iu_slope3 = (iu - iu3) / max(abs(iu3), 1e-9)
        width_slope3 = float(d.get('bb_width_slope3') or 0.0)
        rising = bool(width_slope3 > 0 and mid_slope3 > 0 and iu_slope3 > 0)
        falling = bool(mid_slope3 < 0 and iu_slope3 < 0)
        regime = 'RISING' if rising else ('FALLING' if falling else 'SIDEWAYS')
        base_entry = bool(d.get('early') or d.get('confirm'))
        breakout_ok = bool(d.get('inner_cross') and float(d.get('price_slope3') or 0) > 0 and float(d.get('volume_ratio') or 0) >= 1.2)
        structure_entry = bool(base_entry and (rising or (regime == 'SIDEWAYS' and breakout_ok)))
        rows.append({
            'time': x['time'], 'open': float(x['open']), 'high': float(x['high']), 'low': float(x['low']), 'close': float(x['close']),
            'score': float(d.get('score') or 0), 'stage': str(d.get('stage') or ''), 'base_entry': base_entry,
            'structure_entry': structure_entry, 'regime': regime, 'rsi_slope1': float(d.get('rsi_slope1') or 0),
            'gap_delta': float(d.get('macd_gap_delta') or 0), 'inner_upper': float(d.get('inner_upper') or x['close']),
            'mid': float(d.get('mid') or x['close']), 'volume_ratio': float(d.get('volume_ratio') or 0),
            'mid_slope3': mid_slope3, 'inner_upper_slope3': iu_slope3, 'width_slope3': width_slope3,
        })
    return pd.DataFrame(rows)


def close_trade(pos, row, reason, price, exit_time):
    pnl = pos.realized_pct + pos.remaining_fraction * (price / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol, 'entry_time': pos.entry_time, 'exit_time': exit_time, 'entry_price': pos.entry_price,
        'exit_price': price, 'pnl_pct': pnl * 100.0, 'score': pos.score, 'stage': pos.stage, 'regime': pos.regime,
        'reason': reason,
    }


def simulate(frames, entry_col):
    events = {}
    for sym, f in frames.items():
        for _, r in f.iterrows():
            events.setdefault(r['time'], []).append((sym, r))
    pos = None
    trades = []
    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]
        if pos is not None:
            match = next((r for s,r in rows if s == pos.symbol), None)
            if match is not None:
                p = float(match['close']); hi = float(match['high']); lo = float(match['low'])
                pos.high_watermark = max(pos.high_watermark, hi)
                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, match, 'SESSION_FORCE_FLAT', p, ts)); pos = None; continue
                if lo <= pos.stop:
                    trades.append(close_trade(pos, match, 'INITIAL_STOP_INTRABAR_LOW', pos.stop, ts)); pos = None; continue
                strong = bool(float(match['rsi_slope1']) > 0 and float(match['gap_delta']) > 0 and p >= float(match['inner_upper']))
                weak = bool(float(match['rsi_slope1']) < 0 and float(match['gap_delta']) < 0)
                pos.momentum_fail_count = pos.momentum_fail_count + 1 if weak else 0
                high_gain = pos.high_watermark / pos.entry_price - 1.0
                drawdown = 1.0 - p / pos.high_watermark if pos.high_watermark > 0 else 0.0
                if not pos.partial_done:
                    if high_gain >= PARTIAL_MIN_GAIN_PCT and not strong:
                        pos.realized_pct += 0.5 * (p / pos.entry_price - 1.0)
                        pos.remaining_fraction = 0.5
                        pos.partial_done = True
                    elif high_gain >= PROFIT_ARM_PCT and drawdown >= PRE_PARTIAL_PULLBACK_PCT:
                        trades.append(close_trade(pos, match, 'PRE_PARTIAL_HIGH_WATER_PULLBACK', p, ts)); pos = None; continue
                    elif pos.momentum_fail_count >= MOMENTUM_FAIL_BARS:
                        trades.append(close_trade(pos, match, 'PRE_PARTIAL_MOMENTUM_FAIL', p, ts)); pos = None; continue
                else:
                    trail = RUNNER_TRAIL_STRONG_PCT if strong else RUNNER_TRAIL_NORMAL_PCT
                    if drawdown >= trail:
                        trades.append(close_trade(pos, match, 'RUNNER_HIGH_WATER_TRAIL', p, ts)); pos = None; continue
                    if weak and p < float(match['inner_upper']):
                        trades.append(close_trade(pos, match, 'RUNNER_MOMENTUM_BREAK', p, ts)); pos = None; continue
                    if p <= float(match['mid']):
                        trades.append(close_trade(pos, match, 'RUNNER_MID_TOUCH', p, ts)); pos = None; continue
        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = [(s,r) for s,r in rows if bool(r[entry_col])]
            if candidates:
                sym, r = max(candidates, key=lambda z: float(z[1]['score']))
                price = float(r['close'])
                pos = Pos(sym, ts, price, price * (1.0 - FALLBACK_RISK_PCT), float(r['score']), str(r['stage']), str(r['regime']), price)
    if pos is not None:
        # close at final available bar for that symbol
        f = frames[pos.symbol]
        r = f.iloc[-1]
        trades.append(close_trade(pos, r, 'END_OF_DATA', float(r['close']), r['time']))
    return pd.DataFrame(trades)


def summary(name, t):
    if t.empty:
        return {'version':name,'trades':0,'wins':0,'losses':0,'win_rate':0,'avg_pct':0,'gross_pct':0,'pf':0,'max_loss_pct':0}
    wins = t[t.pnl_pct > 0]; losses = t[t.pnl_pct <= 0]
    gp = wins.pnl_pct.sum(); gl = -losses.pnl_pct.sum()
    return {
        'version': name, 'trades': len(t), 'wins': len(wins), 'losses': len(losses),
        'win_rate': round(len(wins) / len(t) * 100, 2), 'avg_pct': round(t.pnl_pct.mean(), 4),
        'gross_pct': round(t.pnl_pct.sum(), 4), 'pf': round(gp / gl, 3) if gl > 0 else float('inf'),
        'max_loss_pct': round(t.pnl_pct.min(), 4),
    }


def main():
    raw = load_data()
    print('KR symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()))
    frames = {}
    for i, (sym, bars) in enumerate(sorted(raw.items()), 1):
        print(f'[{i}/{len(raw)}] diagnostics {sym} bars={len(bars)}', flush=True)
        frames[sym] = enrich(sym, bars)
    base = simulate(frames, 'base_entry')
    struct = simulate(frames, 'structure_entry')
    print('\n=== SUMMARY ===')
    print(pd.DataFrame([summary('V2_BASE', base), summary('V2.1_STRUCTURE', struct)]).to_string(index=False))
    print('\n=== BASE REGIME ===')
    if not base.empty:
        print(base.groupby('regime').agg(trades=('pnl_pct','size'), win_rate=('pnl_pct',lambda s: (s>0).mean()*100), avg_pct=('pnl_pct','mean'), gross_pct=('pnl_pct','sum')).round(4).to_string())
    print('\n=== STRUCTURE REGIME ===')
    if not struct.empty:
        print(struct.groupby('regime').agg(trades=('pnl_pct','size'), win_rate=('pnl_pct',lambda s: (s>0).mean()*100), avg_pct=('pnl_pct','mean'), gross_pct=('pnl_pct','sum')).round(4).to_string())
    base.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v2_base_trades.csv', index=False)
    struct.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v21_structure_trades.csv', index=False)
    print('\nCSV saved: dbb_kr_v2_base_trades.csv, dbb_kr_v21_structure_trades.csv')


if __name__ == '__main__':
    main()
