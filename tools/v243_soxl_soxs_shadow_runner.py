#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_server.analytics import ticks_to_bars
from live_server.strategy_core_v1 import Action, PositionState
from live_server.strategy_pair_selector_v1 import choose_single_entry
from live_server.double_bollinger_v1 import DoubleBollingerV1, DoubleBollingerConfig
from live_server.williams_clean_v1 import CleanWilliamsV1, WilliamsConfig

SYMBOLS = ("SOXL", "SOXS")


def load_ticks(db_path: str, symbol: str, limit: int = 12000):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT symbol,price,qty,cum_volume,ts FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def dbb_eval(engine, db_path: str):
    candidates = []
    histories = {}
    for sym in SYMBOLS:
        ticks = load_ticks(db_path, sym)
        bars = ticks_to_bars(ticks, 1)
        histories[sym] = bars
        if bars.empty:
            continue
        candidates.append(engine.evaluate_flat(sym, bars))
    return choose_single_entry(candidates), histories


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/home/ubuntu/day-trader-api/daytrader.db")
    ap.add_argument("--engine", choices=["DOUBLE_BOLLINGER", "WILLIAMS"], default="DOUBLE_BOLLINGER")
    ap.add_argument("--poll-sec", type=float, default=5.0)
    ap.add_argument("--fallback-risk-pct", type=float, default=0.015)
    ap.add_argument("--max-swing-risk-pct", type=float, default=0.025)
    ap.add_argument("--log", default="/home/ubuntu/day-trader-api/shadow_v243.jsonl")
    args = ap.parse_args()

    if args.engine == "WILLIAMS":
        raise SystemExit("WILLIAMS_LIVE_SHADOW_BLOCKED: session open/previous range adapter not wired yet")

    engine = DoubleBollingerV1(DoubleBollingerConfig(
        fallback_risk_pct=args.fallback_risk_pct,
        max_swing_risk_pct=args.max_swing_risk_pct,
        profit_check_pct=None,
    ))

    state = None
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("=== V243 SOXL/SOXS SHADOW RUNNER ===", flush=True)
    print("ORDER=NONE FINDER=OFF ENGINE=DOUBLE_BOLLINGER SYMBOLS=SOXL,SOXS", flush=True)

    while True:
        now = datetime.now(timezone.utc).isoformat()
        try:
            histories = {sym: ticks_to_bars(load_ticks(args.db, sym), 1) for sym in SYMBOLS}
            if state is None:
                candidates = []
                for sym in SYMBOLS:
                    bars = histories[sym]
                    if not bars.empty:
                        candidates.append(engine.evaluate_flat(sym, bars))
                chosen = choose_single_entry(candidates)
                event = {
                    "ts": now,
                    "mode": "FLAT",
                    "signals": [
                        {"symbol": r.symbol, "action": r.action.value, "reason": r.reason, "price": r.price, "score": r.score, "stop": r.stop, "diagnostics": r.diagnostics}
                        for r in candidates
                    ],
                    "chosen": None,
                }
                if chosen is not None:
                    state = PositionState(chosen.symbol)
                    state.open(chosen.price, chosen.stop, opened_at=now)
                    event["chosen"] = {"symbol": chosen.symbol, "price": chosen.price, "stop": chosen.stop, "reason": chosen.reason, "score": chosen.score}
                    print("SHADOW_ENTER", json.dumps(event["chosen"], ensure_ascii=False), flush=True)
            else:
                bars = histories.get(state.symbol)
                if bars is None or bars.empty:
                    event = {"ts": now, "mode": "OPEN", "symbol": state.symbol, "action": "HOLD", "reason": "NO_BARS"}
                else:
                    res = engine.evaluate_open(state, bars)
                    event = {"ts": now, "mode": "OPEN", "symbol": state.symbol, "action": res.action.value, "reason": res.reason, "price": res.price, "stop": res.stop, "diagnostics": res.diagnostics}
                    if res.action == Action.FULL_EXIT:
                        print("SHADOW_EXIT", json.dumps(event, ensure_ascii=False), flush=True)
                        state = None
                    elif res.action == Action.PARTIAL_EXIT:
                        print("SHADOW_PARTIAL", json.dumps(event, ensure_ascii=False), flush=True)
                        state.partial_exit(res.exit_fraction or 0.5)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except KeyboardInterrupt:
            print("STOPPED", flush=True)
            return
        except Exception as e:
            err = {"ts": now, "error": type(e).__name__, "detail": str(e)}
            print("ERROR", json.dumps(err, ensure_ascii=False), flush=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(err, ensure_ascii=False) + "\n")
        time.sleep(max(1.0, args.poll_sec))


if __name__ == "__main__":
    main()
