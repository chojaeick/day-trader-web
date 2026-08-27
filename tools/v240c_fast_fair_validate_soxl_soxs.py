#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import (
    SYMBOLS, load_1m_bars, make_symbol_histories, williams_daily, metrics
)
from live_server.strategy_core_v1 import Action, PositionState
from live_server.strategy_pair_selector_v1 import choose_single_entry
from live_server.williams_clean_v1 import CleanWilliamsV1, WilliamsConfig
from live_server.double_bollinger_v1 import DoubleBollingerV1, DoubleBollingerConfig


def replay_fast(engine_name: str, engine, histories: Dict[str, pd.DataFrame], cost_bps: float,
                window_1m: int = 300, progress_every: int = 5000):
    idx_by_time: Dict[pd.Timestamp, Dict[str, int]] = {}
    et_lookup = {}
    date_lookup = {}
    for sym, h in histories.items():
        for i, row in h.iterrows():
            t = pd.Timestamp(row["et"]).tz_convert("UTC")
            idx_by_time.setdefault(t, {})[sym] = i
            et_lookup[t] = pd.Timestamp(row["et"])
            date_lookup[t] = row["date_et"]
    times = sorted(idx_by_time)
    daily = {sym: williams_daily(h) for sym, h in histories.items()} if engine_name == "WILLIAMS" else {}

    state: Optional[PositionState] = None
    entry_time = entry_price = entry_reason = None
    mfe = 0.0
    mae = 0.0
    trades = []
    started = time.monotonic()

    def hist_window(sym: str, i: int) -> pd.DataFrame:
        a = max(0, i - window_1m + 1)
        return histories[sym].iloc[a:i + 1].copy()

    def close_trade(t, price, reason):
        nonlocal state, entry_time, entry_price, entry_reason, mfe, mae
        gross = float(price) / float(entry_price) - 1.0
        net = gross - 2.0 * cost_bps / 10000.0
        trades.append({
            "engine": engine_name,
            "symbol": state.symbol,
            "entry_time": str(entry_time),
            "exit_time": str(t),
            "entry_price": float(entry_price),
            "exit_price": float(price),
            "entry_reason": entry_reason,
            "exit_reason": reason,
            "gross_return": gross,
            "net_return": net,
            "mfe": mfe,
            "mae": mae,
        })
        state = None
        entry_time = entry_price = entry_reason = None
        mfe = mae = 0.0

    total = len(times)
    for n, t in enumerate(times, 1):
        if progress_every and (n == 1 or n % progress_every == 0 or n == total):
            elapsed = time.monotonic() - started
            rate = n / elapsed if elapsed > 0 else 0.0
            eta = (total - n) / rate if rate > 0 else 0.0
            print(f"{engine_name}_PROGRESS {n}/{total} trades={len(trades)} elapsed={elapsed:.1f}s eta={eta:.1f}s", flush=True)

        et = et_lookup[t]
        current_date = date_lookup[t]

        if state is not None:
            sym = state.symbol
            if sym in idx_by_time[t]:
                i = idx_by_time[t][sym]
                h = hist_window(sym, i)
                bar = histories[sym].iloc[i]
                mfe = max(mfe, float(bar["high"]) / entry_price - 1.0)
                mae = min(mae, float(bar["low"]) / entry_price - 1.0)
                if et.hour == 15 and et.minute >= 59:
                    close_trade(t, float(bar["close"]), "SESSION_CLOSE")
                    continue
                res = engine.evaluate_open(state, h)
                if res.action == Action.FULL_EXIT:
                    close_trade(t, res.price, res.reason)
                    continue
                if res.action == Action.PARTIAL_EXIT:
                    state.partial_exit(res.exit_fraction or 0.5)

        if state is None:
            candidates = []
            for sym in SYMBOLS:
                if sym not in idx_by_time[t]:
                    continue
                i = idx_by_time[t][sym]
                h = hist_window(sym, i)
                if engine_name == "WILLIAMS":
                    d = daily[sym].get(current_date)
                    if not d:
                        continue
                    res = engine.evaluate_flat(sym, h, **d)
                else:
                    res = engine.evaluate_flat(sym, h)
                candidates.append(res)
            chosen = choose_single_entry(candidates)
            if chosen is not None:
                state = PositionState(chosen.symbol)
                state.open(chosen.price, chosen.stop, opened_at=str(t))
                entry_time = t
                entry_price = chosen.price
                entry_reason = chosen.reason
                mfe = mae = 0.0

    if state is not None:
        h = histories[state.symbol]
        bar = h.iloc[-1]
        close_trade(pd.Timestamp(bar["et"]).tz_convert("UTC"), float(bar["close"]), "END_OF_DATA")
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/home/ubuntu/day-trader-api/daytrader.db")
    ap.add_argument("--max-days", type=int, default=135)
    ap.add_argument("--cost-bps", type=float, default=8.0)
    ap.add_argument("--fallback-risk-pct", type=float, default=0.015)
    ap.add_argument("--max-swing-risk-pct", type=float, default=0.025)
    ap.add_argument("--window-1m", type=int, default=300)
    ap.add_argument("--progress-every", type=int, default=5000)
    ap.add_argument("--out-dir", default="/home/ubuntu/day-trader-api/validation_v240c")
    args = ap.parse_args()

    print("=== V240C FAST FAIR SOXL/SOXS TWO-ENGINE VALIDATION ===", flush=True)
    print("CORE_MUTATION=NONE FINDER=OFF SINGLE_POSITION=YES", flush=True)
    print("WINDOW_1M=", args.window_1m, "COMMON_RISK=", args.fallback_risk_pct, args.max_swing_risk_pct, "COST_BPS=", args.cost_bps, flush=True)

    bars, table = load_1m_bars(args.db, 0)
    dates = sorted(bars["date_et"].unique())
    if args.max_days > 0:
        dates = dates[-args.max_days:]
        bars = bars[bars["date_et"].isin(dates)].copy().reset_index(drop=True)
    print("SOURCE_TABLE=", table, flush=True)
    print("ACTUAL_TRADING_DATES=", len(dates), "FIRST=", dates[0] if dates else None, "LAST=", dates[-1] if dates else None, flush=True)
    print("BARS=", len(bars), "PER_SYMBOL=", bars.groupby("symbol").size().to_dict(), flush=True)

    histories = make_symbol_histories(bars)
    williams = CleanWilliamsV1(WilliamsConfig(
        fallback_risk_pct=args.fallback_risk_pct,
        max_swing_risk_pct=args.max_swing_risk_pct,
        partial_at_r=None,
    ))
    dbb = DoubleBollingerV1(DoubleBollingerConfig(
        fallback_risk_pct=args.fallback_risk_pct,
        max_swing_risk_pct=args.max_swing_risk_pct,
        profit_check_pct=None,
    ))

    wt = replay_fast("WILLIAMS", williams, histories, args.cost_bps, args.window_1m, args.progress_every)
    wm = metrics(wt)
    print("WILLIAMS_METRICS=", json.dumps(wm, ensure_ascii=False), flush=True)

    dt = replay_fast("DOUBLE_BOLLINGER", dbb, histories, args.cost_bps, args.window_1m, args.progress_every)
    dm = metrics(dt)
    print("DOUBLE_BOLLINGER_METRICS=", json.dumps(dm, ensure_ascii=False), flush=True)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(wt).to_csv(out / "williams_trades.csv", index=False)
    pd.DataFrame(dt).to_csv(out / "double_bollinger_trades.csv", index=False)
    summary = {
        "source_table": table,
        "dates": len(dates),
        "window_1m": args.window_1m,
        "cost_bps": args.cost_bps,
        "fallback_risk_pct": args.fallback_risk_pct,
        "max_swing_risk_pct": args.max_swing_risk_pct,
        "williams": wm,
        "double_bollinger": dm,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    def eligible(m):
        return m["trades"] >= 20 and m["pf"] > 1.0 and m["net_pct"] > 0.0
    if eligible(wm) and not eligible(dm):
        winner = "WILLIAMS"
    elif eligible(dm) and not eligible(wm):
        winner = "DOUBLE_BOLLINGER"
    elif eligible(wm) and eligible(dm):
        wk = (wm["pf"], wm["net_pct"], wm["max_dd_pct"])
        dk = (dm["pf"], dm["net_pct"], dm["max_dd_pct"])
        winner = "WILLIAMS" if wk > dk else ("DOUBLE_BOLLINGER" if dk > wk else "TIE")
    else:
        winner = "NONE_CORE_NOT_READY"
    print("WINNER_PRELIMINARY=", winner, flush=True)
    print("OUTPUT_DIR=", out, flush=True)


if __name__ == "__main__":
    main()
