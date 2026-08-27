#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_server.api import db, ticks_to_bars
from live_server.double_bollinger_v2 import DoubleBollingerV2, DoubleBollingerPairV2

LIMITS = {"SOXL": 200000, "SOXS": 80000}


def bars_for(sym: str):
    ticks = db.ticks(sym, LIMITS[sym])
    bars = ticks_to_bars(ticks, 1)
    if bars is None:
        return None
    return bars.copy().reset_index(drop=True)


def compact(d):
    if not isinstance(d, dict):
        return d
    keys = [
        "symbol", "time", "price", "score", "stage", "early", "confirm",
        "rsi", "rsi_slope1", "rsi_slope3", "rsi_accel", "rsi_cross50",
        "macd", "signal", "macd_gap", "macd_gap_delta", "macd_gap_accel",
        "golden_cross", "macd_bull", "macd_approaching",
        "inner_upper", "outer_upper", "inner_cross", "above_inner",
        "price_slope3", "bb_width", "bb_width_slope1", "bb_width_slope3",
        "bb_expanding", "volume_ratio", "minutes_from_open", "component_scores",
    ]
    return {k: d.get(k) for k in keys if k in d}


def main():
    eng = DoubleBollingerV2()
    pair = DoubleBollingerPairV2(eng)
    bars = {s: bars_for(s) for s in ("SOXL", "SOXS")}

    print("=== V255 DOUBLE BOLLINGER PAIR SHADOW ===")
    for sym in ("SOXL", "SOXS"):
        if bars[sym] is None:
            print(sym, "NO_BARS")
            continue
        d = eng.entry_diagnostics(sym, bars[sym])
        print(sym, json.dumps(compact(d), ensure_ascii=False, default=str))

    result = pair.evaluate_flat_pair({k: v for k, v in bars.items() if v is not None})
    print("PAIR", json.dumps({
        "symbol": result.symbol,
        "action": result.action.value,
        "reason": result.reason,
        "price": result.price,
        "score": result.score,
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
