from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, load_data, summary

OUT = Path('/home/ubuntu/day-trader-api')
MIN_TRADES = 40
THRESHOLDS = [50, 55, 60, 65, 70, 75]
INITIAL_STOPS = [0.008, 0.010, 0.012, 0.015]


@dataclass
class Pos:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    entry_score: float
    remaining_fraction: float = 1.0
    realized_pct: float = 0.0
    first_tp_done: bool = False
    rebound_armed: bool = False
    extra_tp_count: int = 0


def to_5m(bars: pd.DataFrame) -> pd.DataFrame:
    x = bars.copy().sort_values('time')
    x['time'] = pd.to_datetime(x['time'])
    x['bucket'] = x['time'].dt.floor('5min')
    g = x.groupby('bucket', sort=True)
    z = g.agg(open=('open','first'), high=('high','max'), low=('low','min'), close=('close','last'), volume=('volume','sum'), rows=('close','size')).reset_index()
    z = z[z.rows >= 5].copy()
    z['time'] = z['bucket'] + pd.Timedelta(minutes=5)
    return z[['time','open','high','low','close','volume']].reset_index(drop=True)


def build_5m_base(raw):
    eng = DoubleBollingerEngine5()
    out = {}
    for sym, bars in sorted(raw.items()):
        f = eng.enrich(to_5m(bars))
        f['symbol'] = sym
        out[sym] = f
    return out


def build_1m_exit_frames(raw, cfg: DoubleBollingerEngine5Config):
    out = {}
    for sym, bars in sorted(raw.items()):
        f = bars.copy().sort_values('time').reset_index(drop=True)
        f['time'] = pd.to_datetime(f['time'])
        c = pd.to_numeric(f['close'], errors='coerce').astype(float)
        mid = c.rolling(cfg.bb_period).mean()
        std = c.rolling(cfg.bb_period).std(ddof=0)
        f['mid_1m'] = mid
        f['inner_upper_1m'] = mid + cfg.inner_sigma * std
        f['inner_lower_1m'] = mid - cfg.inner_sigma * std
        f['outer_upper_1m'] = mid + cfg.outer_sigma * std
        out[sym] = f
    return out


def reweight(frames, cfg: DoubleBollingerEngine5Config, threshold: float):
    out = {}
    for sym, f0 in frames.items():
        f = f0.copy()
        f['score_trend'] = np.where(f['trend_up'], cfg.w_trend, 0.0)
        f['score_macd_state'] = np.where(f['macd_above_signal'], cfg.w_macd_state, 0.0)
        f['score_macd_gap'] = cfg.w_macd_gap * np.clip(f['macd_slope_spread_strength'].fillna(0.0), 0.0, 1.0)
        f['score_golden'] = np.where(f['macd_golden_cross'], cfg.w_golden, 0.0)
        f['score_rsi_state'] = cfg.w_rsi_state * np.clip(f['rsi_slope_strength'].fillna(0.0), 0.0, 1.0)
        f['score_rsi_accel'] = np.where(f['rsi_accelerating'], cfg.w_rsi_accel, 0.0)
        vol_strength = np.clip((f['volume_ratio'].fillna(0.0) - 1.0) / max(cfg.volume_full_ratio - 1.0, 1e-9), 0.0, 1.0)
        f['score_volume'] = cfg.w_volume * vol_strength
        width_strength = np.clip(f['outer_width_ratio'].fillna(0.0) / max(cfg.outer_expand_full_ratio, 1e-9), 0.0, 1.0)
        f['score_outer_expand'] = cfg.w_outer_expand * width_strength
        f['score_inner_traverse'] = np.where(f['inner_traverse_up'], cfg.w_inner_traverse, 0.0)
        cols = ['score_trend','score_macd_state','score_macd_gap','score_golden','score_rsi_state','score_rsi_accel','score_volume','score_outer_expand','score_inner_traverse']
        f['entry_score'] = f[cols].sum(axis=1).clip(0.0, 100.0)
        f['entry_signal'] = f['trend_up'] & (f['entry_score'] >= float(threshold))
        out[sym] = f
    return out


def build_1m_events(exit_frames):
    ev = {}
    for sym, f in exit_frames.items():
        for _, r in f.iterrows():
            ev.setdefault(pd.Timestamp(r.time), []).append((sym, r))
    return ev


def build_entry_events(entry_frames):
    ev = {}
    for sym, f in entry_frames.items():
        q = f[f.entry_signal]
        for _, r in q.iterrows():
            ev.setdefault(pd.Timestamp(r.time), []).append((sym, r))
    return ev


def realize_fraction(pos: Pos, fraction_of_original: float, price: float):
    fraction = min(float(fraction_of_original), pos.remaining_fraction)
    if fraction <= 0:
        return
    pos.realized_pct += fraction * (float(price) / pos.entry_price - 1.0)
    pos.remaining_fraction -= fraction


def close_trade(pos: Pos, price: float, ts, reason: str):
    pnl = pos.realized_pct + pos.remaining_fraction * (float(price) / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol,
        'entry_time': pos.entry_time,
        'exit_time': pd.Timestamp(ts),
        'entry_price': pos.entry_price,
        'exit_price': float(price),
        'entry_score': pos.entry_score,
        'pnl_pct': pnl * 100.0,
        'first_tp_done': pos.first_tp_done,
        'partial_done': pos.first_tp_done,
        'extra_tp_count': pos.extra_tp_count,
        'remaining_before_final': pos.remaining_fraction,
        'reason': reason,
    }


def simulate(exit_events, entry_frames, stop_pct: float):
    entry_events = build_entry_events(entry_frames)
    pos = None
    trades = []
    collisions = 0

    for ts in sorted(exit_events):
        minute = ts.hour * 60 + ts.minute
        rows = exit_events[ts]

        if pos is not None:
            r = next((x for s, x in rows if s == pos.symbol), None)
            if r is not None:
                close = float(r.close)
                low = float(r.low)
                high = float(r.high)
                iu = float(r.inner_upper_1m) if pd.notna(r.inner_upper_1m) else np.nan
                il = float(r.inner_lower_1m) if pd.notna(r.inner_lower_1m) else np.nan
                ou = float(r.outer_upper_1m) if pd.notna(r.outer_upper_1m) else np.nan

                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, close, ts, 'SESSION_FORCE_FLAT'))
                    pos = None
                    continue

                if not pos.first_tp_done:
                    stop_price = pos.entry_price * (1.0 - float(stop_pct))
                    if low <= stop_price:
                        trades.append(close_trade(pos, stop_price, ts, 'INITIAL_STOP'))
                        pos = None
                        continue

                    if np.isfinite(ou) and low > ou:
                        realize_fraction(pos, 0.50, close)
                        pos.first_tp_done = True
                        pos.rebound_armed = False
                        if pos.remaining_fraction <= 1e-9:
                            trades.append(close_trade(pos, close, ts, 'OUTER_FULL_BREAK_TP1'))
                            pos = None
                            continue
                else:
                    if np.isfinite(il) and low <= il:
                        trades.append(close_trade(pos, close, ts, 'INNER_LOWER_FULL_EXIT'))
                        pos = None
                        continue

                    armed_before = pos.rebound_armed
                    if armed_before and np.isfinite(ou) and high >= ou:
                        sell_fraction = pos.remaining_fraction * 0.50
                        realize_fraction(pos, sell_fraction, close)
                        pos.extra_tp_count += 1
                        pos.rebound_armed = False

                    if (not armed_before) and np.isfinite(iu) and low <= iu:
                        pos.rebound_armed = True

        if pos is None and minute < NO_ENTRY_MINUTE:
            c = entry_events.get(ts, [])
            if c:
                if len(c) > 1:
                    collisions += 1
                sym, r = max(c, key=lambda z: (float(z[1].entry_score), float(z[1].macd_slope_spread_strength), float(z[1].rsi_slope_strength), z[0]))
                pos = Pos(sym, pd.Timestamp(ts), float(r.close), float(r.entry_score))

    if pos is not None:
        last_rows = exit_events[max(exit_events)]
        r = next((x for s, x in last_rows if s == pos.symbol), None)
        if r is not None:
            trades.append(close_trade(pos, float(r.close), r.time, 'END_OF_DATA'))

    return pd.DataFrame(trades), collisions


def metric_row(name, t, collisions, cfg, threshold, stop_pct):
    if 'partial_done' not in t.columns:
        t = t.copy()
        t['partial_done'] = t['first_tp_done'] if 'first_tp_done' in t.columns else False
    r = summary(name, t)
    r.update({
        'threshold': threshold,
        'initial_stop_pct': stop_pct * 100.0,
        'collisions': collisions,
        'w_macd_state': cfg.w_macd_state,
        'w_macd_gap': cfg.w_macd_gap,
        'w_rsi_state': cfg.w_rsi_state,
        'w_rsi_accel': cfg.w_rsi_accel,
        'macd_full_ratio': cfg.macd_slope_spread_full_ratio,
        'rsi_full_ratio': cfg.rsi_slope_full_ratio,
        'first_tp_rate': round(float(t.first_tp_done.mean() * 100.0), 2) if len(t) and 'first_tp_done' in t.columns else 0.0,
        'avg_extra_tp': round(float(t.extra_tp_count.mean()), 3) if len(t) and 'extra_tp_count' in t.columns else 0.0,
    })
    return r


def candidate_configs():
    base = DoubleBollingerEngine5Config()
    yield 'BASE', base

    for mg, rs in product([15, 20, 25, 30], [15, 20, 25, 30]):
        yield f'W_M{mg}_R{rs}', replace(base, w_macd_gap=float(mg), w_rsi_state=float(rs))

    for mr, rr in product([1.0, 1.5, 2.0, 3.0], [1.0, 1.5, 2.0, 3.0]):
        yield f'S_M{mr}_R{rr}', replace(base, macd_slope_spread_full_ratio=float(mr), rsi_slope_full_ratio=float(rr))

    for accel, vol, outer in product([5, 10, 15], [0, 5, 10], [0, 5, 10]):
        yield f'C_A{accel}_V{vol}_O{outer}', replace(base, w_rsi_accel=float(accel), w_volume=float(vol), w_outer_expand=float(outer))


def main():
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)
    base_5m = build_5m_base(raw)
    exit_frames = build_1m_exit_frames(raw, DoubleBollingerEngine5Config())
    exit_events = build_1m_events(exit_frames)

    rows = []
    best_trades = None
    best_key = None
    configs = list(candidate_configs())
    total = len(configs) * len(THRESHOLDS) * len(INITIAL_STOPS)
    print(f'[TUNER] configs={len(configs)} thresholds={len(THRESHOLDS)} stops={len(INITIAL_STOPS)} total_runs={total}', flush=True)
    print('[ENTRY] 5m trend-up mandatory; MACD-vs-signal slope spread + steep RSI upslope are magnitude scores.', flush=True)
    print('[EXIT] 1m full candle above outer-upper => sell 50%; inner-upper retest arms next outer touch => sell half remaining; inner-lower touch => exit all.', flush=True)
    print('[GOAL] 80% win rate is a target only, never a strategy/filter condition.', flush=True)

    nrun = 0
    for cfg_name, cfg in configs:
        cfg_frames = {}
        eng = DoubleBollingerEngine5(cfg)
        for sym, bars in raw.items():
            cfg_frames[sym] = eng.enrich(to_5m(bars))
        for th in THRESHOLDS:
            frames = reweight(cfg_frames, cfg, th)
            for stop_pct in INITIAL_STOPS:
                t, collisions = simulate(exit_events, frames, stop_pct)
                nrun += 1
                r = metric_row(cfg_name, t, collisions, cfg, th, stop_pct)
                rows.append(r)
                if len(t) >= MIN_TRADES:
                    key = (float(r['win_rate']), len(t), float(r['pf']), float(r['avg_pct']), float(r['gross_pct']))
                    if best_key is None or key > best_key:
                        best_key = key
                        best_trades = t.assign(config=cfg_name, threshold=th, initial_stop_pct=stop_pct * 100.0)
                if nrun % 100 == 0:
                    print(f'[PROGRESS] {nrun}/{total}', flush=True)

    board = pd.DataFrame(rows)
    eligible = board[board.trades >= MIN_TRADES].copy()
    eligible = eligible.sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])

    print('\n=== ENGINE 5 CLARIFIED LOGIC: TOP 30 ===')
    cols = ['version','threshold','initial_stop_pct','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','first_tp_rate','avg_extra_tp','collisions','w_macd_gap','w_rsi_state','w_rsi_accel','w_volume','w_outer_expand','macd_full_ratio','rsi_full_ratio']
    print(eligible[[c for c in cols if c in eligible.columns]].head(30).to_string(index=False))

    board.to_csv(OUT / 'dbb_engine5_clarified_all.csv', index=False)
    eligible.head(100).to_csv(OUT / 'dbb_engine5_clarified_top100.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT / 'dbb_engine5_clarified_best_trades.csv', index=False)
    print('[CSV] dbb_engine5_clarified_all.csv, dbb_engine5_clarified_top100.csv, dbb_engine5_clarified_best_trades.csv')


if __name__ == '__main__':
    main()
