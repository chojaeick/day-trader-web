from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerV2Config
from live_server.double_bollinger_v22 import DoubleBollingerV22ExitPolicy

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
SOURCE = 'kiwoom_ka10080'
MIN_BARS = 40
NO_ENTRY_MINUTE = 15 * 60
FORCE_FLAT_MINUTE = 15 * 60 + 20

# Legacy V2/V2.1 exit settings. Kept unchanged for a fair comparison with the
# currently running A/B test.
FALLBACK_RISK_PCT = 0.012
PROFIT_ARM_PCT = 0.004
PARTIAL_MIN_GAIN_PCT = 0.005
PRE_PARTIAL_PULLBACK_PCT = 0.0035
RUNNER_TRAIL_STRONG_PCT = 0.008
RUNNER_TRAIL_NORMAL_PCT = 0.005
MOMENTUM_FAIL_BARS = 2

V22_POLICY = DoubleBollingerV22ExitPolicy()


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
    q = """select symbol, ts, open, high, low, close, volume
           from historical_minute_bars
           where source=? and interval_min=1
           order by ts,symbol"""
    df = pd.read_sql_query(q, con, params=(SOURCE,))
    con.close()
    df['time'] = pd.to_datetime(df['ts'], errors='coerce')
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['time', 'open', 'high', 'low', 'close']).copy()
    return {
        s: g[['time', 'open', 'high', 'low', 'close', 'volume']]
        .sort_values('time').reset_index(drop=True)
        for s, g in df.groupby('symbol')
    }


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
        il = float(b['inner_lower'].iloc[-1])

        mid_slope3 = (mid - mid3) / max(abs(mid3), 1e-9)
        iu_slope3 = (iu - iu3) / max(abs(iu3), 1e-9)
        width_slope3 = float(d.get('bb_width_slope3') or 0.0)

        rising = bool(width_slope3 > 0 and mid_slope3 > 0 and iu_slope3 > 0)
        falling = bool(mid_slope3 < 0 and iu_slope3 < 0)
        regime = 'RISING' if rising else ('FALLING' if falling else 'SIDEWAYS')

        base_entry = bool(d.get('early') or d.get('confirm'))
        breakout_ok = bool(
            d.get('inner_cross')
            and float(d.get('price_slope3') or 0.0) > 0
            and float(d.get('volume_ratio') or 0.0) >= 1.2
        )
        structure_entry = bool(base_entry and (rising or (regime == 'SIDEWAYS' and breakout_ok)))

        rows.append({
            'time': x['time'],
            'open': float(x['open']),
            'high': float(x['high']),
            'low': float(x['low']),
            'close': float(x['close']),
            'score': float(d.get('score') or 0.0),
            'stage': str(d.get('stage') or ''),
            'base_entry': base_entry,
            'structure_entry': structure_entry,
            'regime': regime,
            'rsi_slope1': float(d.get('rsi_slope1') or 0.0),
            'gap_delta': float(d.get('macd_gap_delta') or 0.0),
            'inner_upper': iu,
            'inner_lower': il,
            'mid': mid,
            'volume_ratio': float(d.get('volume_ratio') or 0.0),
            'mid_slope3': mid_slope3,
            'inner_upper_slope3': iu_slope3,
            'width_slope3': width_slope3,
        })
    return pd.DataFrame(rows)


def close_trade(pos, reason, price, exit_time):
    pnl = pos.realized_pct + pos.remaining_fraction * (price / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol,
        'entry_time': pos.entry_time,
        'exit_time': exit_time,
        'entry_price': pos.entry_price,
        'exit_price': price,
        'pnl_pct': pnl * 100.0,
        'score': pos.score,
        'stage': pos.stage,
        'regime': pos.regime,
        'partial_done': pos.partial_done,
        'reason': reason,
    }


def build_events(frames):
    events = {}
    for sym, f in frames.items():
        for _, r in f.iterrows():
            events.setdefault(r['time'], []).append((sym, r))
    return events


def simulate_legacy(frames, entry_col):
    events = build_events(frames)
    pos = None
    trades = []

    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]

        if pos is not None:
            match = next((r for s, r in rows if s == pos.symbol), None)
            if match is not None:
                p = float(match['close'])
                hi = float(match['high'])
                lo = float(match['low'])
                pos.high_watermark = max(pos.high_watermark, hi)

                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, 'SESSION_FORCE_FLAT', p, ts))
                    pos = None
                    continue

                if lo <= pos.stop:
                    trades.append(close_trade(pos, 'INITIAL_STOP_INTRABAR_LOW', pos.stop, ts))
                    pos = None
                    continue

                strong = bool(
                    float(match['rsi_slope1']) > 0
                    and float(match['gap_delta']) > 0
                    and p >= float(match['inner_upper'])
                )
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
                        trades.append(close_trade(pos, 'PRE_PARTIAL_HIGH_WATER_PULLBACK', p, ts))
                        pos = None
                        continue
                    elif pos.momentum_fail_count >= MOMENTUM_FAIL_BARS:
                        trades.append(close_trade(pos, 'PRE_PARTIAL_MOMENTUM_FAIL', p, ts))
                        pos = None
                        continue
                else:
                    trail = RUNNER_TRAIL_STRONG_PCT if strong else RUNNER_TRAIL_NORMAL_PCT
                    if drawdown >= trail:
                        trades.append(close_trade(pos, 'RUNNER_HIGH_WATER_TRAIL', p, ts))
                        pos = None
                        continue
                    if weak and p < float(match['inner_upper']):
                        trades.append(close_trade(pos, 'RUNNER_MOMENTUM_BREAK', p, ts))
                        pos = None
                        continue
                    if p <= float(match['mid']):
                        trades.append(close_trade(pos, 'RUNNER_MID_TOUCH', p, ts))
                        pos = None
                        continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = [(s, r) for s, r in rows if bool(r[entry_col])]
            if candidates:
                sym, r = max(candidates, key=lambda z: float(z[1]['score']))
                price = float(r['close'])
                pos = Pos(
                    sym, ts, price, price * (1.0 - FALLBACK_RISK_PCT),
                    float(r['score']), str(r['stage']), str(r['regime']), price
                )

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        trades.append(close_trade(pos, 'END_OF_DATA', float(r['close']), r['time']))

    return pd.DataFrame(trades)


def simulate_v22(frames):
    """V2.1 structure entry + V2.2 exit policy.

    The only ordinary full-exit trigger is a completed 1-minute close below the
    inner-lower band. Intrabar band wicks do not count. RSI/MACD deterioration,
    mid touches and high-water pullbacks are diagnostic only, not sell orders.
    A catastrophic 1.2% hard stop remains for risk containment.
    """
    events = build_events(frames)
    pos = None
    trades = []

    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]

        if pos is not None:
            match = next((r for s, r in rows if s == pos.symbol), None)
            if match is not None:
                p = float(match['close'])
                hi = float(match['high'])
                lo = float(match['low'])
                inner_lower = float(match['inner_lower'])
                pos.high_watermark = max(pos.high_watermark, hi)

                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, 'SESSION_FORCE_FLAT', p, ts))
                    pos = None
                    continue

                # Safety stop is intentionally separate from Bollinger structure.
                if lo <= pos.stop:
                    trades.append(close_trade(pos, 'V22_CATASTROPHIC_STOP', pos.stop, ts))
                    pos = None
                    continue

                # TP1 = exactly 2R from the initial risk distance. Fill at target
                # when the bar high reaches it; the remaining 50% becomes runner.
                if not pos.partial_done:
                    tp1 = V22_POLICY.tp1_price(pos.entry_price)
                    if hi >= tp1:
                        pos.realized_pct += 0.5 * (tp1 / pos.entry_price - 1.0)
                        pos.remaining_fraction = 0.5
                        pos.partial_done = True

                # Structural full exit. A wick below the band is ignored because
                # only the completed 1m close is compared with inner-lower.
                if V22_POLICY.candle_closed_below_inner_lower(p, inner_lower):
                    reason = 'V22_RUNNER_INNER_LOWER_CLOSE' if pos.partial_done else 'V22_PRE_TP1_INNER_LOWER_CLOSE'
                    trades.append(close_trade(pos, reason, p, ts))
                    pos = None
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = [(s, r) for s, r in rows if bool(r['structure_entry'])]
            if candidates:
                sym, r = max(candidates, key=lambda z: float(z[1]['score']))
                price = float(r['close'])
                pos = Pos(
                    sym, ts, price, V22_POLICY.initial_stop(price),
                    float(r['score']), str(r['stage']), str(r['regime']), price
                )

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        trades.append(close_trade(pos, 'END_OF_DATA', float(r['close']), r['time']))

    return pd.DataFrame(trades)


def summary(name, t):
    if t.empty:
        return {
            'version': name, 'trades': 0, 'wins': 0, 'losses': 0,
            'win_rate': 0, 'avg_pct': 0, 'avg_win_pct': 0, 'avg_loss_pct': 0,
            'gross_pct': 0, 'pf': 0, 'max_loss_pct': 0, 'partial_rate': 0,
        }

    wins = t[t.pnl_pct > 0]
    losses = t[t.pnl_pct <= 0]
    gp = wins.pnl_pct.sum()
    gl = -losses.pnl_pct.sum()
    return {
        'version': name,
        'trades': len(t),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(t) * 100, 2),
        'avg_pct': round(t.pnl_pct.mean(), 4),
        'avg_win_pct': round(wins.pnl_pct.mean(), 4) if not wins.empty else 0,
        'avg_loss_pct': round(losses.pnl_pct.mean(), 4) if not losses.empty else 0,
        'gross_pct': round(t.pnl_pct.sum(), 4),
        'pf': round(gp / gl, 3) if gl > 0 else float('inf'),
        'max_loss_pct': round(t.pnl_pct.min(), 4),
        'partial_rate': round(t.partial_done.mean() * 100, 2),
    }


def print_regime(name, t):
    print(f'\n=== {name} REGIME ===')
    if t.empty:
        print('no trades')
        return
    print(
        t.groupby('regime').agg(
            trades=('pnl_pct', 'size'),
            win_rate=('pnl_pct', lambda s: (s > 0).mean() * 100),
            avg_pct=('pnl_pct', 'mean'),
            gross_pct=('pnl_pct', 'sum'),
        ).round(4).to_string()
    )


def print_exit_reasons(name, t):
    print(f'\n=== {name} EXIT REASONS ===')
    if t.empty:
        print('no trades')
        return
    print(
        t.groupby('reason').agg(
            trades=('pnl_pct', 'size'),
            win_rate=('pnl_pct', lambda s: (s > 0).mean() * 100),
            avg_pct=('pnl_pct', 'mean'),
            gross_pct=('pnl_pct', 'sum'),
        ).round(4).sort_values('trades', ascending=False).to_string()
    )


def main():
    raw = load_data()
    print('KR symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()))

    frames = {}
    for i, (sym, bars) in enumerate(sorted(raw.items()), 1):
        print(f'[{i}/{len(raw)}] diagnostics {sym} bars={len(bars)}', flush=True)
        frames[sym] = enrich(sym, bars)

    base = simulate_legacy(frames, 'base_entry')
    struct = simulate_legacy(frames, 'structure_entry')
    v22 = simulate_v22(frames)

    print('\n=== SUMMARY ===')
    print(pd.DataFrame([
        summary('V2_BASE_LEGACY_EXIT', base),
        summary('V2.1_STRUCTURE_LEGACY_EXIT', struct),
        summary('V2.2_STRUCTURE_2R_INNERLOWER_EXIT', v22),
    ]).to_string(index=False))

    print_regime('V2_BASE', base)
    print_regime('V2.1_STRUCTURE', struct)
    print_regime('V2.2_STRUCTURE_EXIT', v22)

    print_exit_reasons('V2_BASE', base)
    print_exit_reasons('V2.1_STRUCTURE', struct)
    print_exit_reasons('V2.2_STRUCTURE_EXIT', v22)

    base.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v2_base_legacy_exit_trades.csv', index=False)
    struct.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v21_structure_legacy_exit_trades.csv', index=False)
    v22.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v22_structure_2r_innerlower_trades.csv', index=False)

    print('\nCSV saved:')
    print('  dbb_kr_v2_base_legacy_exit_trades.csv')
    print('  dbb_kr_v21_structure_legacy_exit_trades.csv')
    print('  dbb_kr_v22_structure_2r_innerlower_trades.csv')


if __name__ == '__main__':
    main()
