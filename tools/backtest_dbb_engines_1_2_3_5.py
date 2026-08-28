from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5
from tools.backtest_dbb_kr_v2_v21_v22 import (
    FORCE_FLAT_MINUTE,
    NO_ENTRY_MINUTE,
    load_data,
    simulate_legacy,
    summary,
)
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached, simulate_v22_adaptive

OUT = Path('/home/ubuntu/day-trader-api')


@dataclass
class E5Pos:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    remaining_fraction: float = 1.0
    realized_pct: float = 0.0
    partial_done: bool = False
    outer_broken: bool = False
    setup_time: pd.Timestamp | None = None


def to_5m(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """Build completed causal 5-minute bars from stored 1-minute bars.

    A bucket containing HH:MM..HH:MM+4 is timestamped at HH:MM+5, i.e. the
    moment when that 5-minute candle is known to be complete. Only full 5-row
    buckets are used so Engine 5 never sees an incomplete synthetic candle.
    """
    x = bars_1m.copy().sort_values('time')
    x['time'] = pd.to_datetime(x['time'])
    x['bucket'] = x['time'].dt.floor('5min')
    g = x.groupby('bucket', sort=True)
    z = g.agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
        rows=('close', 'size'),
    ).reset_index()
    z = z[z['rows'] >= 5].copy()
    z['time'] = z['bucket'] + pd.Timedelta(minutes=5)
    return z[['time', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)


def build_engine5_frames(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    engine = DoubleBollingerEngine5()
    out = {}
    for sym, bars in sorted(raw.items()):
        b5 = to_5m(bars)
        f = engine.enrich(b5)
        f['symbol'] = sym
        out[sym] = f
        print(
            f'[E5 5M] {sym} bars={len(b5)} signals={int(f.entry_signal.fillna(False).sum())} '
            f'setups={int(f.setup_event.fillna(False).sum())}',
            flush=True,
        )
    return out


def build_e5_events(frames):
    events = {}
    for sym, f in frames.items():
        for _, r in f.iterrows():
            events.setdefault(pd.Timestamp(r['time']), []).append((sym, r))
    return events


def close_e5(pos: E5Pos, price: float, ts, reason: str) -> dict:
    pnl = pos.realized_pct + pos.remaining_fraction * (float(price) / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol,
        'entry_time': pos.entry_time,
        'exit_time': pd.Timestamp(ts),
        'entry_price': pos.entry_price,
        'exit_price': float(price),
        'pnl_pct': pnl * 100.0,
        'partial_done': pos.partial_done,
        'reason': reason,
        'setup_time': pos.setup_time,
    }


def simulate_engine5(frames):
    """Simulate only the user-stated Engine 5 strategy rules.

    Strategy exits:
      1) Dynamic outer-upper breakout then return below current outer-upper:
         realize 50% at the completed 5m bar close.
      2) MACD slope < 0 AND RSI slope < 0 AND the same completed 5m bar has
         reached inner-lower (low <= inner-lower): liquidate the remainder at
         that bar close. If TP1 never occurred, this liquidates 100%.

    There is deliberately NO fixed TP, NO fixed stop, NO trailing stop, NO
    score threshold, and NO extra RSI/MACD exit.

    The only non-strategy accounting rule is the same intraday FORCE_FLAT used
    by Engines 1/2/3, so all engines are compared on the same session boundary.
    """
    events = build_e5_events(frames)
    pos = None
    trades = []
    consumed_setup = {}
    collisions = 0

    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]

        if pos is not None:
            match = next((r for s, r in rows if s == pos.symbol), None)
            if match is not None:
                p = float(match['close'])
                hi = float(match['high'])
                lo = float(match['low'])
                ou = float(match['outer_upper']) if pd.notna(match['outer_upper']) else np.nan
                il = float(match['inner_lower']) if pd.notna(match['inner_lower']) else np.nan

                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_e5(pos, p, ts, 'SESSION_FORCE_FLAT'))
                    pos = None
                    continue

                # Dynamic TP1: outer-upper is recalculated every completed 5m bar.
                # The target therefore rises automatically while the band expands.
                if not pos.partial_done and np.isfinite(ou):
                    if hi >= ou:
                        pos.outer_broken = True
                    if pos.outer_broken and p < ou:
                        pos.realized_pct += 0.5 * (p / pos.entry_price - 1.0)
                        pos.remaining_fraction = 0.5
                        pos.partial_done = True

                # Full exit is exactly the stated conjunction. The inner-lower
                # touch is intrabar, while MACD/RSI directions are known at the
                # completed 5m bar; execution is therefore priced at that close.
                full_exit = bool(
                    float(match['macd_slope']) < 0.0
                    and float(match['rsi_slope']) < 0.0
                    and np.isfinite(il)
                    and lo <= il
                )
                if full_exit:
                    reason = 'E5_FULL_EXIT_MACD_RSI_INNER_LOWER'
                    trades.append(close_e5(pos, p, ts, reason))
                    pos = None
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = []
            for sym, r in rows:
                if not bool(r.get('entry_signal', False)):
                    continue
                age = r.get('setup_age')
                if pd.isna(age):
                    continue
                # Identify the setup event from the current bar index-age by time.
                setup_time = pd.Timestamp(ts) - pd.Timedelta(minutes=5 * int(age))
                if consumed_setup.get(sym) == setup_time:
                    continue
                candidates.append((sym, r, setup_time))

            if candidates:
                if len(candidates) > 1:
                    collisions += 1
                # Engine 5 has no score. When simultaneous symbols are eligible,
                # use the stated volume expansion only as deterministic arbitration.
                sym, r, setup_time = max(
                    candidates,
                    key=lambda z: (float(z[1].get('volume_ratio') or 0.0), z[0]),
                )
                price = float(r['close'])
                pos = E5Pos(sym, pd.Timestamp(ts), price, setup_time=setup_time)
                consumed_setup[sym] = setup_time

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        trades.append(close_e5(pos, float(r['close']), r['time'], 'END_OF_DATA'))

    t = pd.DataFrame(trades)
    print(f'[E5] simultaneous-entry collisions={collisions}', flush=True)
    return t


def metric_row(name: str, t: pd.DataFrame) -> dict:
    r = summary(name, t)
    r['median_pct'] = round(float(t.pnl_pct.median()), 4) if not t.empty else 0.0
    return r


def print_exit_reasons(name: str, t: pd.DataFrame):
    print(f'\n=== {name} EXIT REASONS ===')
    if t.empty:
        print('no trades')
        return
    rows = []
    for reason, g in t.groupby('reason'):
        wins = g[g.pnl_pct > 0]
        losses = g[g.pnl_pct <= 0]
        gp = float(wins.pnl_pct.sum())
        gl = float(-losses.pnl_pct.sum())
        rows.append({
            'reason': reason,
            'trades': len(g),
            'win_rate': round((g.pnl_pct > 0).mean() * 100.0, 2),
            'avg_pct': round(float(g.pnl_pct.mean()), 4),
            'gross_pct': round(float(g.pnl_pct.sum()), 4),
            'pf': round(gp / gl, 3) if gl > 0 else np.inf,
        })
    print(pd.DataFrame(rows).sort_values('gross_pct', ascending=False).to_string(index=False))


def main():
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)

    # Engines 1/2/3 are kept exactly as already validated.
    frames_1m = build_frames_cached(raw, workers=2, rebuild=False)
    e1 = simulate_legacy(frames_1m, 'base_entry')
    e2 = simulate_legacy(frames_1m, 'structure_entry')
    e3 = simulate_v22_adaptive(frames_1m, 'structure_entry')

    # Engine 5 uses only 5-minute candles and the user-stated logic above.
    frames_5m = build_engine5_frames(raw)
    e5 = simulate_engine5(frames_5m)

    board = pd.DataFrame([
        metric_row('ENGINE_1_V2_BASE_1M', e1),
        metric_row('ENGINE_2_V21_STRUCTURE_1M', e2),
        metric_row('ENGINE_3_V22_ADAPTIVE_1M', e3),
        metric_row('ENGINE_5_USER_LOGIC_5M', e5),
    ])

    cols = [
        'version', 'trades', 'wins', 'losses', 'win_rate', 'avg_pct',
        'avg_win_pct', 'avg_loss_pct', 'gross_pct', 'pf', 'max_loss_pct',
        'partial_rate', 'median_pct',
    ]
    print('\n=== ENGINES 1 / 2 / 3 / 5 COMPARISON ===')
    print(board[[c for c in cols if c in board.columns]].to_string(index=False))

    print_exit_reasons('ENGINE_5_USER_LOGIC_5M', e5)

    board.to_csv(OUT / 'dbb_engines_1_2_3_5_summary.csv', index=False)
    e5.to_csv(OUT / 'dbb_engine5_trades.csv', index=False)

    print('\n[ENGINE 5 LOCKED LOGIC]')
    print('timeframe=5m')
    print('trend=8 completed 5m DBB-mid slope > 0')
    print('setup=within recent 8 bars price traversed inner-lower -> inner-upper with volume >= 2x prior-8-bar average')
    print('entry=setup active AND MACD>signal AND RSI slope>0 AND outer band expanding')
    print('tp1=dynamic outer-upper breakout, then completed 5m close returns below current outer-upper -> sell 50%')
    print('runner=hold through inner-upper; no trail/fixed TP')
    print('full_exit=MACD slope<0 AND RSI slope<0 AND bar low reaches inner-lower -> sell all remainder at completed-bar close')
    print('no fixed stop; only common session force-flat for apples-to-apples intraday accounting')
    print('[CSV] dbb_engines_1_2_3_5_summary.csv, dbb_engine5_trades.csv')


if __name__ == '__main__':
    main()
