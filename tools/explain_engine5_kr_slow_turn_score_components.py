from __future__ import annotations

"""Explain why selected KR Slow-turn cases received their original Engine5 score.

This is a read-only diagnostic. It rebuilds the same KR scored 5m frames used by
show_engine5_kr_slow_turn_original_scores.py and prints the exact score-component
breakdown plus the underlying indicator state for selected timestamps.
"""

from dataclasses import replace
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

CASES = [
    ("122630", "2026-08-20 13:06:00+09:00", "LOSS_NEAR"),
    ("058610", "2026-08-13 09:25:00+09:00", "WIN_V_REBOUND_LIKE"),
]

SCORE_COLS = [
    "score_trend", "score_macd_state", "score_macd_gap", "score_golden",
    "score_rsi_state", "score_rsi_accel", "score_volume",
    "score_outer_expand", "score_inner_traverse",
]
FEATURE_COLS = [
    "entry_score", "trend_up", "macd", "macd_signal", "macd_above_signal",
    "macd_gap", "macd_gap_delta", "macd_slope", "macd_signal_slope",
    "macd_slope_spread", "macd_slope_spread_strength", "macd_golden_cross",
    "rsi", "rsi_slope", "rsi_accel", "rsi_slope_strength", "rsi_accelerating",
    "volume_ratio", "outer_width_ratio", "inner_traverse_up",
]


def n(x):
    return str(x).zfill(6)


def rebuild_scored():
    raw = {n(k): v for k, v in load_data().items()}
    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f.copy() for s, f in reweight(f10, cfg, 0.0).items()}
    for f in scored.values():
        f["time"] = pd.to_datetime(f["time"])
        f.sort_values("time", inplace=True)
    return scored


def row_at(scored, sym, ts):
    target = pd.Timestamp(ts).floor("5min")
    f = scored[n(sym)]
    q = f[f.time <= target]
    if q.empty:
        raise RuntimeError(f"no row for {sym} {ts}")
    return q.iloc[-1]


def fmt(v):
    if isinstance(v, bool):
        return str(v)
    try:
        x = float(v)
        return f"{x:.4f}"
    except Exception:
        return str(v)


def main():
    scored = rebuild_scored()
    print("=== KR SLOW-TURN ORIGINAL SCORE COMPONENT AUDIT ===")
    print("Read-only. Original Engine5 score only; no forced score=50.\n")

    for sym, ts, label in CASES:
        r = row_at(scored, sym, ts)
        print(f"=== {label} | {sym} | requested={ts} | score_bar={r['time']} ===")
        total = 0.0
        for c in SCORE_COLS:
            v = float(pd.to_numeric(pd.Series([r.get(c)]), errors="coerce").fillna(0.0).iloc[0])
            total += v
            print(f"{c:24s} {v:8.4f}")
        print(f"{'SUM':24s} {total:8.4f}")
        print(f"{'entry_score':24s} {fmt(r.get('entry_score'))}")
        print("-- indicator state --")
        for c in FEATURE_COLS[1:]:
            print(f"{c:24s} {fmt(r.get(c))}")
        print()

    print("READING:")
    print("- trend_up contributes a flat 20 points; macd_above_signal contributes a flat 10 points.")
    print("- MACD score is slope-spread relative strength, not raw MACD upslope magnitude.")
    print("- RSI score is one-bar RSI-slope relative strength; acceleration adds a flat 10 points.")
    print("- Therefore an old/gradual uptrend can accumulate a high score, while a fresh V-turn can lose 30 points if trend_up=False and MACD is still below signal.")


if __name__ == "__main__":
    main()
