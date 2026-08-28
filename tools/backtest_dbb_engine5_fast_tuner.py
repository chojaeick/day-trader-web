from __future__ import annotations

import time
from dataclasses import replace
from itertools import product
from pathlib import Path

import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import (
    INITIAL_STOPS,
    MIN_TRADES,
    THRESHOLDS,
    build_1m_events,
    build_1m_exit_frames,
    metric_row,
    reweight,
    simulate,
    to_5m,
)
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api')
COARSE_STOP = 0.010
TOP_CONFIGS = 12


def candidate_configs():
    base = DoubleBollingerEngine5Config()
    yield 'BASE', base
    for mg, rs in product([15, 20, 25, 30], [15, 20, 25, 30]):
        yield f'W_M{mg}_R{rs}', replace(base, w_macd_gap=float(mg), w_rsi_state=float(rs))
    for mr, rr in product([1.0, 1.5, 2.0, 3.0], [1.0, 1.5, 2.0, 3.0]):
        yield f'S_M{mr}_R{rr}', replace(base, macd_slope_spread_full_ratio=float(mr), rsi_slope_full_ratio=float(rr))
    for accel, vol, outer in product([5, 10, 15], [0, 5, 10], [0, 5, 10]):
        yield f'C_A{accel}_V{vol}_O{outer}', replace(base, w_rsi_accel=float(accel), w_volume=float(vol), w_outer_expand=float(outer))


def cfg_signature(cfg: DoubleBollingerEngine5Config):
    return (
        cfg.w_macd_gap, cfg.w_rsi_state, cfg.w_rsi_accel, cfg.w_volume, cfg.w_outer_expand,
        cfg.macd_slope_spread_full_ratio, cfg.rsi_slope_full_ratio,
    )


def build_cfg_frames(raw, cfg):
    eng = DoubleBollingerEngine5(cfg)
    return {sym: eng.enrich(to_5m(bars)) for sym, bars in raw.items()}


def rank_key(r):
    return (float(r['win_rate']), int(r['trades']), float(r['pf']), float(r['avg_pct']), float(r['gross_pct']))


def main():
    t0 = time.perf_counter()
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)

    base_cfg = DoubleBollingerEngine5Config()
    exit_frames = build_1m_exit_frames(raw, base_cfg)
    exit_events = build_1m_events(exit_frames)
    print(f'[FAST] 1m exit events built once: {len(exit_events)} timestamps', flush=True)

    configs = list(candidate_configs())
    frame_cache = {}
    coarse_rows = []
    coarse_trades = {}
    coarse_total = len(configs) * len(THRESHOLDS)
    n = 0

    print(f'[FAST STAGE 1] configs={len(configs)} thresholds={len(THRESHOLDS)} stop={COARSE_STOP*100:.1f}% runs={coarse_total}', flush=True)
    print('[FAST RULE] Stage 1 ranks entry configurations with one neutral stop. No 80% win-rate filter.', flush=True)

    for cfg_name, cfg in configs:
        sig = cfg_signature(cfg)
        if sig not in frame_cache:
            frame_cache[sig] = build_cfg_frames(raw, cfg)
        cfg_frames = frame_cache[sig]
        for th in THRESHOLDS:
            frames = reweight(cfg_frames, cfg, th)
            trades, collisions = simulate(exit_events, frames, COARSE_STOP)
            r = metric_row(cfg_name, trades, collisions, cfg, th, COARSE_STOP)
            coarse_rows.append(r)
            coarse_trades[(cfg_name, th)] = trades
            n += 1
            if n % 30 == 0 or n == coarse_total:
                print(f'[FAST PROGRESS 1] {n}/{coarse_total} elapsed={time.perf_counter()-t0:.1f}s', flush=True)

    coarse = pd.DataFrame(coarse_rows)
    eligible = coarse[coarse.trades >= MIN_TRADES].copy()
    eligible = eligible.sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])
    top_pairs = eligible[['version','threshold']].drop_duplicates().head(TOP_CONFIGS)

    cfg_map = {name: cfg for name, cfg in configs}
    final_rows = []
    best_trades = None
    best_key = None
    fine_total = len(top_pairs) * len(INITIAL_STOPS)
    n2 = 0
    print(f'[FAST STAGE 2] top_configs={len(top_pairs)} stops={len(INITIAL_STOPS)} runs={fine_total}', flush=True)

    for _, pick in top_pairs.iterrows():
        cfg_name = str(pick['version'])
        th = float(pick['threshold'])
        cfg = cfg_map[cfg_name]
        sig = cfg_signature(cfg)
        cfg_frames = frame_cache[sig]
        frames = reweight(cfg_frames, cfg, th)
        for stop_pct in INITIAL_STOPS:
            trades, collisions = simulate(exit_events, frames, float(stop_pct))
            r = metric_row(cfg_name, trades, collisions, cfg, th, float(stop_pct))
            final_rows.append(r)
            if len(trades) >= MIN_TRADES:
                k = rank_key(r)
                if best_key is None or k > best_key:
                    best_key = k
                    best_trades = trades.assign(config=cfg_name, threshold=th, initial_stop_pct=float(stop_pct)*100.0)
            n2 += 1
            if n2 % 12 == 0 or n2 == fine_total:
                print(f'[FAST PROGRESS 2] {n2}/{fine_total} elapsed={time.perf_counter()-t0:.1f}s', flush=True)

    board = pd.DataFrame(final_rows)
    board = board[board.trades >= MIN_TRADES].copy().sort_values(
        ['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False]
    )

    print('\n=== ENGINE 5 FAST TUNER: TOP 30 ===')
    cols = ['version','threshold','initial_stop_pct','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','first_tp_rate','avg_extra_tp','collisions','w_macd_gap','w_rsi_state','w_rsi_accel','w_volume','w_outer_expand','macd_full_ratio','rsi_full_ratio']
    print(board[[c for c in cols if c in board.columns]].head(30).to_string(index=False))

    coarse.to_csv(OUT / 'dbb_engine5_fast_stage1.csv', index=False)
    board.head(100).to_csv(OUT / 'dbb_engine5_fast_top100.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT / 'dbb_engine5_fast_best_trades.csv', index=False)
    print(f'[TIMING] total={time.perf_counter()-t0:.2f}s')
    print('[CSV] dbb_engine5_fast_stage1.csv, dbb_engine5_fast_top100.csv, dbb_engine5_fast_best_trades.csv')


if __name__ == '__main__':
    main()
