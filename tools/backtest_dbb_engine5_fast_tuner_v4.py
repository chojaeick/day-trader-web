from __future__ import annotations

import time
from dataclasses import replace
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import MIN_TRADES, THRESHOLDS, build_1m_exit_frames, reweight, to_5m
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, load_data, summary

OUT = Path('/home/ubuntu/day-trader-api')
CHECKPOINT = OUT / 'dbb_engine5_exit_v5_checkpoint.csv'


def candidate_configs():
    base = DoubleBollingerEngine5Config()
    yield 'BASE', base
    for mg, rs in product([15, 20, 25, 30], [15, 20, 25, 30]):
        yield f'W_M{mg}_R{rs}', replace(base, w_macd_gap=float(mg), w_rsi_state=float(rs))
    for mr, rr in product([1.0, 1.5, 2.0, 3.0], [1.0, 1.5, 2.0, 3.0]):
        yield f'S_M{mr}_R{rr}', replace(base, macd_slope_spread_full_ratio=float(mr), rsi_slope_full_ratio=float(rr))
    for accel, vol, outer in product([5, 10, 15], [0, 5, 10], [0, 5, 10]):
        yield f'C_A{accel}_V{vol}_O{outer}', replace(base, w_rsi_accel=float(accel), w_volume=float(vol), w_outer_expand=float(outer))


def build_cfg_frames(raw, cfg):
    eng = DoubleBollingerEngine5(cfg)
    return {sym: eng.enrich(to_5m(bars)) for sym, bars in raw.items()}


def _finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def pack_exit_events(raw, cfg):
    exit_frames = build_1m_exit_frames(raw, cfg)
    by_time = {}
    for sym, f in exit_frames.items():
        cols = ['time', 'close', 'low', 'high', 'inner_upper_1m', 'inner_lower_1m', 'outer_upper_1m']
        for r in f[cols].itertuples(index=False, name=None):
            ts = pd.Timestamp(r[0])
            by_time.setdefault(ts, {})[sym] = (
                float(r[1]), float(r[2]), float(r[3]), _finite(r[4]), _finite(r[5]), _finite(r[6])
            )
    return [(ts, ts.hour * 60 + ts.minute, rows) for ts, rows in sorted(by_time.items())]


def pack_state_events(frames):
    ev = {}
    for sym, f in frames.items():
        cols = ['time', 'trend_up', 'outer_expanding', 'mid_slope8', 'macd_slope_spread', 'rsi_slope']
        for ts, trend_up, outer_expanding, mid_slope8, spread, rsi_slope in f[cols].itertuples(index=False, name=None):
            ev.setdefault(pd.Timestamp(ts), {})[sym] = (
                bool(trend_up), bool(outer_expanding), _finite(mid_slope8), _finite(spread), _finite(rsi_slope)
            )
    return ev


def pack_entry_events(scored_frames):
    ev = {}
    for sym, f in scored_frames.items():
        if 'entry_gate' not in f.columns:
            raise RuntimeError('Engine 5 frame missing entry_gate; corrected persistence gate is not deployed')
        q = f[f['entry_gate']].copy()
        cols = ['time', 'close', 'entry_score', 'macd_slope_spread_strength', 'rsi_slope_strength', 'inner_upper', 'inner_lower', 'outer_upper', 'mid']
        for r in q[cols].itertuples(index=False, name=None):
            iu = _finite(r[5]); il = _finite(r[6]); ou = _finite(r[7]); mid = _finite(r[8]); close = float(r[1])
            band_r = iu - il if np.isfinite(iu) and np.isfinite(il) else np.nan
            if not np.isfinite(band_r) or band_r <= 0:
                continue
            # Structural R: if entry is already extended above the upper bands,
            # do not use the tiny current inner-band width alone. Anchor risk to
            # at least the distance back to inner-upper, while keeping one full
            # inner-band width as the minimum structural unit.
            extended_dist = max(0.0, close - iu) if np.isfinite(iu) else 0.0
            structural_r = max(band_r, extended_dist)
            ts = pd.Timestamp(r[0])
            ev.setdefault(ts, []).append((sym, close, float(r[2]), _finite(r[3]), _finite(r[4]), structural_r, iu, il, ou, mid, band_r))
    return ev


def simulate_v4(packed_exits, entry_events, state_events, threshold: float):
    """Engine 5 exit V5.

    R is structural risk at entry:
      max(full 5m inner-band width, entry-close distance back to inner-upper).
    - Initial stop: entry - 1R.
    - TP1: entry + 2R, sell 50% original.
    - After TP1, continuing uptrend + outer expansion: outer-upper touch sells
      half the remainder (25% original).
    - Final runner exits on 1m close below inner-lower.
    - Momentum/trend fade exits all remaining shares immediately at the current
      1m close when at least two of the following are true on the latest 5m bar:
        DBB mid slope <= 0, MACD slope spread <= 0, RSI slope <= 0.
      This is evaluated after TP1 and also while profitable before TP1, so a
      strong move is not allowed to round-trip while waiting for a mechanical TP.
    - Re-entry is allowed with no artificial cooldown when entry_gate becomes true again.
    """
    pos = None
    trades = []
    collisions = 0
    current_state = {}
    last_price = None
    last_ts = None

    def realize(fraction_of_original, price):
        nonlocal pos
        fraction = min(float(fraction_of_original), pos['remaining'])
        if fraction <= 0:
            return
        pos['realized'] += fraction * (float(price) / pos['entry_price'] - 1.0)
        pos['remaining'] -= fraction

    def close_record(price, ts, reason):
        nonlocal pos
        pnl = pos['realized'] + pos['remaining'] * (float(price) / pos['entry_price'] - 1.0)
        trades.append({
            'symbol': pos['symbol'], 'entry_time': pos['entry_time'], 'exit_time': pd.Timestamp(ts),
            'entry_price': pos['entry_price'], 'exit_price': float(price), 'entry_score': pos['entry_score'],
            'r_abs': pos['r_abs'], 'raw_band_r': pos['raw_band_r'], 'r_pct': pos['r_abs'] / pos['entry_price'] * 100.0,
            'stop_price': pos['stop_price'], 'tp1_price': pos['tp1_price'],
            'pnl_pct': pnl * 100.0, 'first_tp_done': pos['tp1_done'], 'second_tp_done': pos['tp2_done'],
            'partial_done': pos['tp1_done'], 'extra_tp_count': int(pos['tp2_done']),
            'remaining_before_final': pos['remaining'], 'reason': reason,
        })
        pos = None

    for ts, minute, rows in packed_exits:
        last_ts = ts
        if ts in state_events:
            current_state.update(state_events[ts])

        if pos is not None:
            rr = rows.get(pos['symbol'])
            if rr is not None:
                close, low, high, iu, il, ou = rr
                last_price = close
                trend_up, outer_expanding, mid_slope8, spread, rsi_slope = current_state.get(
                    pos['symbol'], (False, False, np.nan, np.nan, np.nan)
                )
                fade_votes = int(np.isfinite(mid_slope8) and mid_slope8 <= 0) + int(np.isfinite(spread) and spread <= 0) + int(np.isfinite(rsi_slope) and rsi_slope <= 0)
                momentum_fade = fade_votes >= 2
                profitable = close > pos['entry_price']

                if minute >= FORCE_FLAT_MINUTE:
                    close_record(close, ts, 'SESSION_FORCE_FLAT')
                elif not pos['tp1_done']:
                    if low <= pos['stop_price']:
                        close_record(pos['stop_price'], ts, 'INITIAL_1R_STOP')
                    elif high >= pos['tp1_price']:
                        realize(0.50, pos['tp1_price'])
                        pos['tp1_done'] = True
                        pos['tp1_time'] = ts
                    elif profitable and momentum_fade:
                        close_record(close, ts, 'PROFIT_MOMENTUM_FADE_EXIT')
                else:
                    if momentum_fade:
                        close_record(close, ts, 'MOMENTUM_FADE_FULL_EXIT')
                    else:
                        if (not pos['tp2_done']) and trend_up and outer_expanding and np.isfinite(ou) and high >= ou:
                            realize(pos['remaining'] * 0.50, ou)
                            pos['tp2_done'] = True
                            pos['tp2_time'] = ts

                        if pos is not None and pos['tp2_done'] and np.isfinite(il) and close < il:
                            close_record(close, ts, 'INNER_LOWER_CLOSE_EXIT')

        if pos is None and minute < NO_ENTRY_MINUTE:
            cands = entry_events.get(ts)
            if cands:
                eligible = [c for c in cands if c[2] >= float(threshold)]
                if eligible:
                    if len(eligible) > 1:
                        collisions += 1
                    sym, close, score, ms, rs, structural_r, entry_iu, entry_il, entry_ou, entry_mid, raw_band_r = max(
                        eligible,
                        key=lambda c: (c[2], c[3] if np.isfinite(c[3]) else -1e9, c[4] if np.isfinite(c[4]) else -1e9, c[0])
                    )
                    pos = {
                        'symbol': sym, 'entry_time': pd.Timestamp(ts), 'entry_price': close, 'entry_score': score,
                        'r_abs': structural_r, 'raw_band_r': raw_band_r,
                        'entry_inner_upper': entry_iu, 'entry_inner_lower': entry_il, 'entry_outer_upper': entry_ou, 'entry_mid': entry_mid,
                        'stop_price': close - structural_r, 'tp1_price': close + 2.0 * structural_r,
                        'remaining': 1.0, 'realized': 0.0, 'tp1_done': False, 'tp2_done': False,
                        'tp1_time': None, 'tp2_time': None,
                    }
                    last_price = close

    if pos is not None and last_price is not None and last_ts is not None:
        close_record(last_price, last_ts, 'END_OF_DATA')

    return pd.DataFrame(trades), collisions


def metric_row(name, t, collisions, cfg, threshold):
    r = summary(name, t)
    r.update({
        'threshold': threshold,
        'collisions': collisions,
        'w_macd_state': cfg.w_macd_state,
        'w_macd_gap': cfg.w_macd_gap,
        'w_rsi_state': cfg.w_rsi_state,
        'w_rsi_accel': cfg.w_rsi_accel,
        'w_volume': cfg.w_volume,
        'w_outer_expand': cfg.w_outer_expand,
        'macd_full_ratio': cfg.macd_slope_spread_full_ratio,
        'rsi_full_ratio': cfg.rsi_slope_full_ratio,
        'first_tp_rate': round(float(t.first_tp_done.mean() * 100.0), 2) if len(t) else 0.0,
        'second_tp_rate': round(float(t.second_tp_done.mean() * 100.0), 2) if len(t) else 0.0,
        'avg_r_pct': round(float(t.r_pct.mean()), 4) if len(t) else np.nan,
    })
    return r


def rank_key(r):
    return (float(r['win_rate']), float(r['pf']), float(r['avg_pct']), float(r['gross_pct']), int(r['trades']))


def main():
    t0 = time.perf_counter()
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)
    base_cfg = DoubleBollingerEngine5Config()
    packed_exits = pack_exit_events(raw, base_cfg)
    base_frames = build_cfg_frames(raw, base_cfg)
    state_events = pack_state_events(base_frames)
    print('[EXIT V5] structural R=max(inner-band width, entry distance to inner-upper); stop=-1R; TP1=+2R sell50%; continuing trend outer-upper touch sells half remaining; momentum fade(2/3: DBB mid, MACD spread, RSI slope) exits remaining; final runner close<inner-lower.', flush=True)
    print(f'[TIMELINE] 1m timestamps={len(packed_exits)}', flush=True)

    existing = pd.read_csv(CHECKPOINT) if CHECKPOINT.exists() else pd.DataFrame()
    rows = existing.to_dict('records') if not existing.empty else []
    done = set()
    if not existing.empty and {'version', 'threshold'}.issubset(existing.columns):
        done = {(str(r.version), round(float(r.threshold), 6)) for r in existing.itertuples()}
        print(f'[RESUME] {CHECKPOINT.name} rows={len(existing)}', flush=True)

    configs = list(candidate_configs())
    total = len(configs) * len(THRESHOLDS)
    n = len(done)
    best_key = None
    best_trades = None

    for cfg_name, cfg in configs:
        pending = [th for th in THRESHOLDS if (cfg_name, round(float(th), 6)) not in done]
        if not pending:
            continue
        ct0 = time.perf_counter()
        frames = build_cfg_frames(raw, cfg)
        scored = reweight(frames, cfg, 0.0)
        entry_events = pack_entry_events(scored)
        for th in pending:
            trades, collisions = simulate_v4(packed_exits, entry_events, state_events, th)
            row = metric_row(cfg_name, trades, collisions, cfg, th)
            rows.append(row)
            n += 1
            if len(trades) >= MIN_TRADES:
                k = rank_key(row)
                if best_key is None or k > best_key:
                    best_key = k
                    best_trades = trades.assign(config=cfg_name, threshold=th)
        pd.DataFrame(rows).drop_duplicates(['version', 'threshold'], keep='last').to_csv(CHECKPOINT, index=False)
        print(f'[PROGRESS] {n}/{total} config={cfg_name} sec={time.perf_counter()-ct0:.2f} total={time.perf_counter()-t0:.1f}s', flush=True)

    board = pd.DataFrame(rows).drop_duplicates(['version', 'threshold'], keep='last')
    eligible = board[board.trades >= MIN_TRADES].copy().sort_values(
        ['win_rate', 'pf', 'avg_pct', 'gross_pct', 'trades'], ascending=[False, False, False, False, False]
    )
    print('\n=== ENGINE 5 EXIT V5: TOP 30 ===')
    cols = ['version','threshold','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','first_tp_rate','second_tp_rate','avg_r_pct','collisions','w_macd_gap','w_rsi_state','w_rsi_accel','w_volume','w_outer_expand','macd_full_ratio','rsi_full_ratio']
    print(eligible[[c for c in cols if c in eligible.columns]].head(30).to_string(index=False))

    board.to_csv(OUT / 'dbb_engine5_exit_v5_all.csv', index=False)
    eligible.head(100).to_csv(OUT / 'dbb_engine5_exit_v5_top100.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT / 'dbb_engine5_exit_v5_best_trades.csv', index=False)
    print(f'[TIMING] total={time.perf_counter()-t0:.2f}s')
    print('[CSV] dbb_engine5_exit_v5_all.csv, dbb_engine5_exit_v5_top100.csv, dbb_engine5_exit_v5_best_trades.csv')


if __name__ == '__main__':
    main()
