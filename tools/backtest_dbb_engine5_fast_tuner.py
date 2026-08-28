from __future__ import annotations

import time
from dataclasses import replace
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import (
    INITIAL_STOPS,
    MIN_TRADES,
    THRESHOLDS,
    build_1m_exit_frames,
    metric_row,
    reweight,
    to_5m,
)
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, load_data

OUT = Path('/home/ubuntu/day-trader-api')
COARSE_STOP = 0.010
TOP_CONFIGS = 12
# New checkpoint names are intentional: old checkpoints were produced by the
# incorrect trend-only gate and must never contaminate corrected validation.
STAGE1_CKPT = OUT / 'dbb_engine5_fast_gates_v2_stage1_checkpoint.csv'
STAGE2_CKPT = OUT / 'dbb_engine5_fast_gates_v2_stage2_checkpoint.csv'


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
    return {(str(r.version), round(float(r.threshold), 6), round(float(r.initial_stop_pct), 6)) for r in df.itertuples()}


def _finite(v):
    try:
        x = float(v)
        return x if np.isfinite(x) else np.nan
    except Exception:
        return np.nan


def pack_exit_events(exit_frames):
    by_time = {}
    for sym, f in exit_frames.items():
        cols = ['time','close','low','high','inner_upper_1m','inner_lower_1m','outer_upper_1m']
        for r in f[cols].itertuples(index=False, name=None):
            ts = pd.Timestamp(r[0])
            by_time.setdefault(ts, {})[sym] = (
                float(r[1]), float(r[2]), float(r[3]), _finite(r[4]), _finite(r[5]), _finite(r[6])
            )
    return [(ts, ts.hour * 60 + ts.minute, rows) for ts, rows in sorted(by_time.items())]


def pack_entry_events(scored_frames):
    ev = {}
    for sym, f in scored_frames.items():
        # Corrected Engine 5 hard gate. Score is not allowed to compensate for
        # a missing directional condition.
        if 'entry_gate' not in f.columns:
            raise RuntimeError('Engine 5 frame missing entry_gate; corrected engine was not deployed')
        q = f[f['entry_gate']]
        cols = ['time','close','entry_score','macd_slope_spread_strength','rsi_slope_strength']
        for r in q[cols].itertuples(index=False, name=None):
            ts = pd.Timestamp(r[0])
            ev.setdefault(ts, []).append((sym, float(r[1]), float(r[2]), _finite(r[3]), _finite(r[4])))
    return ev


def simulate_fast(packed_exits, entry_events, threshold: float, stop_pct: float):
    pos = None
    trades = []
    collisions = 0
    last_price = None
    last_ts = None

    def close_record(price, ts, reason):
        nonlocal pos
        sym, et, ep, esc, rem, realized, tp1, armed, extra = pos
        pnl = realized + rem * (float(price) / ep - 1.0)
        trades.append({
            'symbol': sym, 'entry_time': et, 'exit_time': pd.Timestamp(ts),
            'entry_price': ep, 'exit_price': float(price), 'entry_score': esc,
            'pnl_pct': pnl * 100.0, 'first_tp_done': tp1, 'partial_done': tp1,
            'extra_tp_count': extra, 'remaining_before_final': rem, 'reason': reason,
        })
        pos = None

    for ts, minute, rows in packed_exits:
        last_ts = ts
        if pos is not None:
            sym, et, ep, esc, rem, realized, tp1, armed, extra = pos
            rr = rows.get(sym)
            if rr is not None:
                close, low, high, iu, il, ou = rr
                last_price = close
                if minute >= FORCE_FLAT_MINUTE:
                    close_record(close, ts, 'SESSION_FORCE_FLAT')
                elif not tp1:
                    stop_price = ep * (1.0 - float(stop_pct))
                    if low <= stop_price:
                        close_record(stop_price, ts, 'INITIAL_STOP')
                    elif np.isfinite(ou) and low > ou:
                        frac = min(0.50, rem)
                        realized += frac * (close / ep - 1.0)
                        rem -= frac
                        tp1 = True
                        armed = False
                        pos = (sym, et, ep, esc, rem, realized, tp1, armed, extra)
                else:
                    if np.isfinite(il) and low <= il:
                        close_record(close, ts, 'INNER_LOWER_FULL_EXIT')
                    else:
                        armed_before = armed
                        if armed_before and np.isfinite(ou) and high >= ou:
                            frac = rem * 0.50
                            realized += frac * (close / ep - 1.0)
                            rem -= frac
                            extra += 1
                            armed = False
                        if (not armed_before) and np.isfinite(iu) and low <= iu:
                            armed = True
                        pos = (sym, et, ep, esc, rem, realized, tp1, armed, extra)

        if pos is None and minute < NO_ENTRY_MINUTE:
            cands = entry_events.get(ts)
            if cands:
                eligible = [c for c in cands if c[2] >= float(threshold)]
                if eligible:
                    if len(eligible) > 1:
                        collisions += 1
                    sym, close, score, ms, rs = max(eligible, key=lambda c: (c[2], c[3] if np.isfinite(c[3]) else -1e9, c[4] if np.isfinite(c[4]) else -1e9, c[0]))
                    pos = (sym, pd.Timestamp(ts), close, score, 1.0, 0.0, False, False, 0)
                    last_price = close

    if pos is not None and last_price is not None and last_ts is not None:
        close_record(last_price, last_ts, 'END_OF_DATA')

    return pd.DataFrame(trades), collisions


def score_once(cfg_frames, cfg):
    # reweight changes only scores; entry_gate comes from the corrected engine
    # and remains attached to every frame.
    return reweight(cfg_frames, cfg, 0.0)


def main():
    t0 = time.perf_counter()
    raw = load_data()
    print(f'[DATA] symbols={len(raw)} 1m_bars={sum(len(x) for x in raw.values())}', flush=True)

    base_cfg = DoubleBollingerEngine5Config()
    exit_frames = build_1m_exit_frames(raw, base_cfg)
    packed_exits = pack_exit_events(exit_frames)
    del exit_frames
    print(f'[ULTRAFAST] packed 1m exit timeline once: {len(packed_exits)} timestamps', flush=True)
    print('[ENTRY GATES V2] REQUIRED: DBB mid rising AND MACD slope>0 AND MACD-signal slope spread>0 AND RSI slope>0.', flush=True)
    print('[ENTRY SCORE] Magnitudes/confirmations rank only candidates that passed all hard gates.', flush=True)
    print('[CHECKPOINT] Using new gates-v2 checkpoint files; old trend-only results are ignored.', flush=True)

    configs = list(candidate_configs())
    cfg_map = {name: cfg for name, cfg in configs}
    coarse_existing = _load_checkpoint(STAGE1_CKPT)
    coarse_rows = coarse_existing.to_dict('records') if not coarse_existing.empty else []
    done1 = _completed_stage1(coarse_existing)
    coarse_total = len(configs) * len(THRESHOLDS)
    n = len(done1)

    print(f'[FAST STAGE 1] configs={len(configs)} thresholds={len(THRESHOLDS)} stop={COARSE_STOP*100:.1f}% runs={coarse_total} already_done={n}', flush=True)

    for cfg_name, cfg in configs:
        pending = [th for th in THRESHOLDS if (cfg_name, round(float(th), 6)) not in done1]
        if not pending:
            continue
        ct0 = time.perf_counter()
        cfg_frames = build_cfg_frames(raw, cfg)
        scored = score_once(cfg_frames, cfg)
        entry_events = pack_entry_events(scored)
        del cfg_frames, scored
        for th in pending:
            trades, collisions = simulate_fast(packed_exits, entry_events, th, COARSE_STOP)
            coarse_rows.append(metric_row(cfg_name, trades, collisions, cfg, th, COARSE_STOP))
            n += 1
        pd.DataFrame(coarse_rows).drop_duplicates(['version','threshold'], keep='last').to_csv(STAGE1_CKPT, index=False)
        print(f'[FAST PROGRESS 1] {n}/{coarse_total} config={cfg_name} sec={time.perf_counter()-ct0:.2f} total={time.perf_counter()-t0:.1f}s', flush=True)

    coarse = pd.DataFrame(coarse_rows).drop_duplicates(['version','threshold'], keep='last')
    eligible = coarse[coarse.trades >= MIN_TRADES].copy().sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])
    top_pairs = eligible[['version','threshold']].drop_duplicates().head(TOP_CONFIGS)

    final_existing = _load_checkpoint(STAGE2_CKPT)
    final_rows = final_existing.to_dict('records') if not final_existing.empty else []
    done2 = _completed_stage2(final_existing)
    fine_total = len(top_pairs) * len(INITIAL_STOPS)
    n2 = sum(1 for _, p in top_pairs.iterrows() for stop in INITIAL_STOPS if (str(p['version']), round(float(p['threshold']),6), round(float(stop)*100.0,6)) in done2)
    print(f'[FAST STAGE 2] top_configs={len(top_pairs)} stops={len(INITIAL_STOPS)} runs={fine_total} already_done={n2}', flush=True)

    best_trades = None
    best_key = None
    for _, pick in top_pairs.iterrows():
        cfg_name = str(pick['version']); th = float(pick['threshold']); cfg = cfg_map[cfg_name]
        pending_stops = [float(s) for s in INITIAL_STOPS if (cfg_name, round(th,6), round(float(s)*100.0,6)) not in done2]
        if not pending_stops:
            continue
        ct0 = time.perf_counter()
        cfg_frames = build_cfg_frames(raw, cfg)
        entry_events = pack_entry_events(score_once(cfg_frames, cfg))
        del cfg_frames
        for stop_pct in pending_stops:
            trades, collisions = simulate_fast(packed_exits, entry_events, th, stop_pct)
            r = metric_row(cfg_name, trades, collisions, cfg, th, stop_pct)
            final_rows.append(r); n2 += 1
            if len(trades) >= MIN_TRADES:
                k = rank_key(r)
                if best_key is None or k > best_key:
                    best_key = k
                    best_trades = trades.assign(config=cfg_name, threshold=th, initial_stop_pct=stop_pct*100.0)
        pd.DataFrame(final_rows).drop_duplicates(['version','threshold','initial_stop_pct'], keep='last').to_csv(STAGE2_CKPT, index=False)
        print(f'[FAST PROGRESS 2] {n2}/{fine_total} config={cfg_name} sec={time.perf_counter()-ct0:.2f} total={time.perf_counter()-t0:.1f}s', flush=True)

    board = pd.DataFrame(final_rows).drop_duplicates(['version','threshold','initial_stop_pct'], keep='last')
    board = board[board.trades >= MIN_TRADES].copy().sort_values(['win_rate','trades','pf','avg_pct','gross_pct'], ascending=[False,False,False,False,False])

    print('\n=== ENGINE 5 GATES V2 FAST TUNER: TOP 30 ===')
    cols = ['version','threshold','initial_stop_pct','trades','wins','losses','win_rate','avg_pct','gross_pct','pf','max_loss_pct','first_tp_rate','avg_extra_tp','collisions','w_macd_gap','w_rsi_state','w_rsi_accel','w_volume','w_outer_expand','macd_full_ratio','rsi_full_ratio']
    print(board[[c for c in cols if c in board.columns]].head(30).to_string(index=False))
    coarse.to_csv(OUT / 'dbb_engine5_fast_gates_v2_stage1.csv', index=False)
    board.head(100).to_csv(OUT / 'dbb_engine5_fast_gates_v2_top100.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(OUT / 'dbb_engine5_fast_gates_v2_best_trades.csv', index=False)
    print(f'[TIMING] total={time.perf_counter()-t0:.2f}s')
    print('[CSV] dbb_engine5_fast_gates_v2_stage1.csv, dbb_engine5_fast_gates_v2_top100.csv, dbb_engine5_fast_gates_v2_best_trades.csv')


if __name__ == '__main__':
    main()
