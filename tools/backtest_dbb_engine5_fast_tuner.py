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
STAGE1_CKPT = OUT / 'dbb_engine5_fast_stage1_checkpoint.csv'
STAGE2_CKPT = OUT / 'dbb_engine5_fast_stage2_checkpoint.csv'


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


def rank_key(r):
    return (float(r['win_rate']), int(r['trades']), float(r['pf']), float(r['avg_pct']), float(r['gross_pct']))


def _load_checkpoint(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        print(f'[RESUME] {path.name} rows={len(df)}', flush=True)
        return df
    except Exception as e:
        print(f'[RESUME WARN] cannot read {path.name}: {e}', flush=True)
        return pd.DataFrame()


def _completed_stage1(df: pd.DataFrame) -> set[tuple[str, float]]:
    if df.empty or 'version' not in df.columns or 'threshold' not in df.columns:
        return set()
    return {(str(r.version), round(float(r.threshold), 6)) for r in df.itertuples()}


def _completed_stage2(df: pd.DataFrame) -> set[tuple[str, float, float]]:
    if df.empty or not {'version', 'threshold', 'initial_stop_pct'}.issubset(df.columns):
        return set()
    return {
        (str(r.version), round(float(r.threshold), 6), round(float(r.initial_stop_pct), 6))
        for r in df.itertuples()
    }


def main():
    t0 = time.perf_counter()
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)

    base_cfg = DoubleBollingerEngine5Config()
    exit_frames = build_1m_exit_frames(raw, base_cfg)
    exit_events = build_1m_events(exit_frames)
    del exit_frames
    print(f'[FAST] 1m exit events built once: {len(exit_events)} timestamps', flush=True)
    print('[MEMORY] Config frames are processed one-at-a-time; no 60-config DataFrame cache is retained.', flush=True)

    configs = list(candidate_configs())
    cfg_map = {name: cfg for name, cfg in configs}

    coarse_existing = _load_checkpoint(STAGE1_CKPT)
    coarse_rows = coarse_existing.to_dict('records') if not coarse_existing.empty else []
    done1 = _completed_stage1(coarse_existing)
    coarse_total = len(configs) * len(THRESHOLDS)
    n = len(done1)

    print(f'[FAST STAGE 1] configs={len(configs)} thresholds={len(THRESHOLDS)} stop={COARSE_STOP*100:.1f}% runs={coarse_total} already_done={n}', flush=True)
    print('[FAST RULE] Stage 1 ranks entry configurations with one neutral stop. No 80% win-rate filter.', flush=True)

    for cfg_name, cfg in configs:
        pending = [th for th in THRESHOLDS if (cfg_name, round(float(th), 6)) not in done1]
        if not pending:
            continue

        cfg_frames = build_cfg_frames(raw, cfg)
        for th in pending:
            frames = reweight(cfg_frames, cfg, th)
            trades, collisions = simulate(exit_events, frames, COARSE_STOP)
            r = metric_row(cfg_name, trades, collisions, cfg, th, COARSE_STOP)
            coarse_rows.append(r)
            n += 1
            if n % 30 == 0 or n == coarse_total:
                print(f'[FAST PROGRESS 1] {n}/{coarse_total} elapsed={time.perf_counter()-t0:.1f}s', flush=True)
        del cfg_frames

        pd.DataFrame(coarse_rows).drop_duplicates(['version', 'threshold'], keep='last').to_csv(STAGE1_CKPT, index=False)

    coarse = pd.DataFrame(coarse_rows).drop_duplicates(['version', 'threshold'], keep='last')
    coarse.to_csv(STAGE1_CKPT, index=False)
    eligible = coarse[coarse.trades >= MIN_TRADES].copy()
    eligible = eligible.sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])
    top_pairs = eligible[['version','threshold']].drop_duplicates().head(TOP_CONFIGS)

    final_existing = _load_checkpoint(STAGE2_CKPT)
    final_rows = final_existing.to_dict('records') if not final_existing.empty else []
    done2 = _completed_stage2(final_existing)
    best_trades = None
    best_key = None
    fine_total = len(top_pairs) * len(INITIAL_STOPS)
    n2 = sum(
        1 for _, pick in top_pairs.iterrows() for stop in INITIAL_STOPS
        if (str(pick['version']), round(float(pick['threshold']), 6), round(float(stop) * 100.0, 6)) in done2
    )
    print(f'[FAST STAGE 2] top_configs={len(top_pairs)} stops={len(INITIAL_STOPS)} runs={fine_total} already_done={n2}', flush=True)

    for _, pick in top_pairs.iterrows():
        cfg_name = str(pick['version'])
        th = float(pick['threshold'])
        cfg = cfg_map[cfg_name]
        pending_stops = [
            float(stop) for stop in INITIAL_STOPS
            if (cfg_name, round(th, 6), round(float(stop) * 100.0, 6)) not in done2
        ]
        if not pending_stops:
            continue

        cfg_frames = build_cfg_frames(raw, cfg)
        frames = reweight(cfg_frames, cfg, th)
        for stop_pct in pending_stops:
            trades, collisions = simulate(exit_events, frames, stop_pct)
            r = metric_row(cfg_name, trades, collisions, cfg, th, stop_pct)
            final_rows.append(r)
            if len(trades) >= MIN_TRADES:
                k = rank_key(r)
                if best_key is None or k > best_key:
                    best_key = k
                    best_trades = trades.assign(config=cfg_name, threshold=th, initial_stop_pct=stop_pct*100.0)
            n2 += 1
            if n2 % 12 == 0 or n2 == fine_total:
                print(f'[FAST PROGRESS 2] {n2}/{fine_total} elapsed={time.perf_counter()-t0:.1f}s', flush=True)
        del cfg_frames

        pd.DataFrame(final_rows).drop_duplicates(['version', 'threshold', 'initial_stop_pct'], keep='last').to_csv(STAGE2_CKPT, index=False)

    board = pd.DataFrame(final_rows).drop_duplicates(['version', 'threshold', 'initial_stop_pct'], keep='last')
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
    print('[CHECKPOINT] Stage 1/2 checkpoints remain on disk so a disconnected SSH session can resume instead of restarting.')


if __name__ == '__main__':
    main()
