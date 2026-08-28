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
THRESHOLDS = [45, 50, 55, 60, 65, 70, 75, 80]
EXIT_MODES = ['TOUCH', 'MACD', 'RSI', 'BOTH']


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


def build_base_frames(raw):
    eng = DoubleBollingerEngine5()
    out = {}
    for sym, bars in sorted(raw.items()):
        f = eng.enrich(to_5m(bars))
        f['symbol'] = sym
        out[sym] = f
    return out


def reweight(frames, cfg: DoubleBollingerEngine5Config, threshold: float):
    out = {}
    for sym, f0 in frames.items():
        f = f0.copy()
        f['score_trend'] = np.where(f['trend_up'], cfg.w_trend, 0.0)
        f['score_macd_state'] = np.where(f['macd_above_signal'], cfg.w_macd_state, 0.0)
        f['score_macd_gap'] = np.where(f['macd_gap_widening'], cfg.w_macd_gap, 0.0)
        f['score_golden'] = np.where(f['macd_golden_cross'], cfg.w_golden, 0.0)
        f['score_rsi_state'] = np.where(f['rsi_slope'] > 0, cfg.w_rsi_state, 0.0)
        f['score_rsi_accel'] = np.where(f['rsi_accelerating'], cfg.w_rsi_accel, 0.0)
        vol_strength = np.clip((f['volume_ratio'].fillna(0.0) - 1.0) / max(cfg.volume_full_ratio - 1.0, 1e-9), 0.0, 1.0)
        f['score_volume'] = cfg.w_volume * vol_strength
        width_strength = np.clip(f['outer_width_ratio'].fillna(0.0) / max(cfg.outer_expand_full_ratio, 1e-9), 0.0, 1.0)
        f['score_outer_expand'] = cfg.w_outer_expand * width_strength
        f['score_inner_traverse'] = np.where(f['inner_traverse_up'], cfg.w_inner_traverse, 0.0)
        cols = ['score_trend','score_macd_state','score_macd_gap','score_golden','score_rsi_state','score_rsi_accel','score_volume','score_outer_expand','score_inner_traverse']
        f['entry_score'] = f[cols].sum(axis=1).clip(0.0, 100.0)
        # Overall rising trend is a mandatory Engine5 strategy condition.
        # Win rate is NOT a strategy gate; it is only an observed tuning result.
        f['entry_signal'] = f['trend_up'] & (f['entry_score'] >= float(threshold))
        out[sym] = f
    return out


def build_events(frames):
    ev = {}
    for sym, f in frames.items():
        for _, r in f.iterrows():
            ev.setdefault(pd.Timestamp(r.time), []).append((sym, r))
    return ev


def should_full_exit(r, mode):
    il = float(r.inner_lower) if pd.notna(r.inner_lower) else np.nan
    if not np.isfinite(il) or float(r.low) > il:
        return False
    macd_down = float(r.macd_slope) < 0.0
    rsi_down = float(r.rsi_slope) < 0.0
    if mode == 'TOUCH':
        return True
    if mode == 'MACD':
        return macd_down
    if mode == 'RSI':
        return rsi_down
    return macd_down and rsi_down


def close_trade(pos, price, ts, reason):
    pnl = pos.realized_pct + pos.remaining_fraction * (float(price) / pos.entry_price - 1.0)
    return {
        'symbol': pos.symbol, 'entry_time': pos.entry_time, 'exit_time': pd.Timestamp(ts),
        'entry_price': pos.entry_price, 'exit_price': float(price), 'entry_score': pos.entry_score,
        'pnl_pct': pnl * 100.0, 'partial_done': pos.partial_done, 'reason': reason,
    }


def simulate(frames, exit_mode):
    ev = build_events(frames)
    pos = None
    trades = []
    collisions = 0
    for ts in sorted(ev):
        minute = ts.hour * 60 + ts.minute
        rows = ev[ts]
        if pos is not None:
            r = next((x for s, x in rows if s == pos.symbol), None)
            if r is not None:
                p = float(r.close)
                ou = float(r.outer_upper) if pd.notna(r.outer_upper) else np.nan
                if minute >= FORCE_FLAT_MINUTE:
                    trades.append(close_trade(pos, p, ts, 'SESSION_FORCE_FLAT'))
                    pos = None
                    continue
                if not pos.partial_done and np.isfinite(ou):
                    if float(r.high) >= ou:
                        pos.outer_broken = True
                    if pos.outer_broken and p < ou:
                        pos.realized_pct += 0.5 * (p / pos.entry_price - 1.0)
                        pos.remaining_fraction = 0.5
                        pos.partial_done = True
                if should_full_exit(r, exit_mode):
                    trades.append(close_trade(pos, p, ts, f'E5_EXIT_{exit_mode}'))
                    pos = None
                    continue
        if pos is None and minute < NO_ENTRY_MINUTE:
            c = [(s, r) for s, r in rows if bool(r.entry_signal)]
            if c:
                if len(c) > 1:
                    collisions += 1
                sym, r = max(c, key=lambda z: (float(z[1].entry_score), float(z[1].volume_ratio or 0.0), z[0]))
                pos = Pos(sym, pd.Timestamp(ts), float(r.close), float(r.entry_score))
    if pos is not None:
        r = frames[pos.symbol].iloc[-1]
        trades.append(close_trade(pos, float(r.close), r.time, 'END_OF_DATA'))
    return pd.DataFrame(trades), collisions


def metric_row(name, t, collisions, cfg, threshold, exit_mode):
    r = summary(name, t)
    r.update({
        'threshold': threshold, 'exit_mode': exit_mode, 'collisions': collisions,
        'w_macd_state': cfg.w_macd_state, 'w_macd_gap': cfg.w_macd_gap, 'w_golden': cfg.w_golden,
        'w_rsi_state': cfg.w_rsi_state, 'w_rsi_accel': cfg.w_rsi_accel, 'w_volume': cfg.w_volume,
        'w_outer_expand': cfg.w_outer_expand, 'w_inner_traverse': cfg.w_inner_traverse,
    })
    return r


def candidate_configs():
    base = DoubleBollingerEngine5Config()
    yield 'BASE', base
    # Focused weight variants: change only one or two dimensions at a time so
    # entry frequency is not crushed by an enormous overfit grid.
    for name, field, vals in [
        ('MACD_STATE','w_macd_state',[10,20,25]),
        ('MACD_GAP','w_macd_gap',[5,15,20]),
        ('GOLDEN','w_golden',[0,10,15]),
        ('RSI_STATE','w_rsi_state',[10,20,25]),
        ('RSI_ACCEL','w_rsi_accel',[5,15,20]),
        ('VOLUME','w_volume',[5,15,20]),
        ('OUTER','w_outer_expand',[5,15,20]),
        ('TRAVERSE','w_inner_traverse',[0,10,15]),
    ]:
        for v in vals:
            yield f'{name}_{v}', replace(base, **{field: float(v)})

    # Compact paired momentum emphasis variants. These are score changes,
    # not new hard entry gates, so tuning does not manufacture win rate by
    # simply starving the engine of entries.
    for macd_gap, rsi_accel, volume, outer in product([10,15,20], [10,15,20], [5,10,15], [5,10,15]):
        cfg = replace(base, w_macd_gap=float(macd_gap), w_rsi_accel=float(rsi_accel), w_volume=float(volume), w_outer_expand=float(outer))
        yield f'PAIR_G{macd_gap}_R{rsi_accel}_V{volume}_O{outer}', cfg


def main():
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)
    base_frames = build_base_frames(raw)
    rows = []
    best_trades = None
    best_key = None

    configs = list(candidate_configs())
    total = len(configs) * len(THRESHOLDS) * len(EXIT_MODES)
    print(f'[TUNER] configs={len(configs)} thresholds={len(THRESHOLDS)} exits={len(EXIT_MODES)} total_runs={total} min_trades={MIN_TRADES}', flush=True)
    print('[RULE] 80% win rate is a tuning goal only. It is not used as a filter or strategy condition.', flush=True)

    nrun = 0
    for cfg_name, cfg in configs:
        for th in THRESHOLDS:
            frames = reweight(base_frames, cfg, th)
            for exit_mode in EXIT_MODES:
                t, collisions = simulate(frames, exit_mode)
                nrun += 1
                r = metric_row(cfg_name, t, collisions, cfg, th, exit_mode)
                rows.append(r)
                if len(t) >= MIN_TRADES:
                    # Rank observed results only. No target-win-rate gate.
                    key = (float(r['win_rate']), len(t), float(r['pf']), float(r['avg_pct']), float(r['gross_pct']))
                    if best_key is None or key > best_key:
                        best_key = key
                        best_trades = t.assign(config=cfg_name, threshold=th, exit_mode=exit_mode)
                if nrun % 100 == 0:
                    print(f'[PROGRESS] {nrun}/{total}', flush=True)

    board = pd.DataFrame(rows)
    eligible = board[board.trades >= MIN_TRADES].copy()
    # Win rate is the primary tuning objective; trades is the second key so
    # equal-win-rate configurations prefer more opportunities, not fewer.
    eligible = eligible.sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])

    print('\n=== ENGINE 5 TUNER: TOP 30 (NO WIN-RATE FILTER) ===')
    cols = ['version','threshold','exit_mode','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','partial_rate','collisions','w_macd_state','w_macd_gap','w_golden','w_rsi_state','w_rsi_accel','w_volume','w_outer_expand','w_inner_traverse']
    print(eligible[[c for c in cols if c in eligible.columns]].head(30).to_string(index=False))

    board.to_csv(OUT / 'dbb_engine5_tuner_all.csv', index=False)
    eligible.head(100).to_csv(OUT / 'dbb_engine5_tuner_top100.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT / 'dbb_engine5_tuner_best_trades.csv', index=False)
    print('[CSV] dbb_engine5_tuner_all.csv, dbb_engine5_tuner_top100.csv, dbb_engine5_tuner_best_trades.csv')


if __name__ == '__main__':
    main()
