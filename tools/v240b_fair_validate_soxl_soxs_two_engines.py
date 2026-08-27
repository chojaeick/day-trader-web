#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow direct execution: python tools/v240b_....py
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars, make_symbol_histories, replay, metrics
from live_server.williams_clean_v1 import CleanWilliamsV1, WilliamsConfig
from live_server.double_bollinger_v1 import DoubleBollingerV1, DoubleBollingerConfig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/home/ubuntu/day-trader-api/daytrader.db")
    ap.add_argument("--max-days", type=int, default=135, help="actual US regular-session trading dates")
    ap.add_argument("--cost-bps", type=float, default=8.0)
    ap.add_argument("--fallback-risk-pct", type=float, default=0.015)
    ap.add_argument("--max-swing-risk-pct", type=float, default=0.025)
    ap.add_argument("--out-dir", default="/home/ubuntu/day-trader-api/validation_v240b")
    args = ap.parse_args()

    print("=== V240B FAIR SOXL/SOXS TWO-ENGINE VALIDATION ===")
    print("SYMBOLS=SOXL,SOXS FINDER=OFF SINGLE_POSITION=YES SESSION=US_REGULAR")
    print("CORE_MUTATION_DURING_TEST=NO")
    print("COMMON_RISK fallback=", args.fallback_risk_pct, "max_swing=", args.max_swing_risk_pct, "cost_bps=", args.cost_bps)

    # Load all available rows, then select actual trading dates, not calendar-day approximation.
    bars, table = load_1m_bars(args.db, 0)
    dates = sorted(bars["date_et"].unique())
    if args.max_days > 0:
        dates = dates[-args.max_days:]
        bars = bars[bars["date_et"].isin(dates)].copy().reset_index(drop=True)
    print("SOURCE_TABLE=", table)
    print("ACTUAL_TRADING_DATES=", len(dates), "FIRST=", dates[0] if dates else None, "LAST=", dates[-1] if dates else None)
    print("BARS=", len(bars), "PER_SYMBOL=", bars.groupby("symbol").size().to_dict())

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

    wt = replay("WILLIAMS", williams, histories, args.cost_bps)
    dt = replay("DOUBLE_BOLLINGER", dbb, histories, args.cost_bps)
    wm = metrics(wt)
    dm = metrics(dt)

    print("WILLIAMS_METRICS=", json.dumps(wm, ensure_ascii=False))
    print("DOUBLE_BOLLINGER_METRICS=", json.dumps(dm, ensure_ascii=False))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(wt).to_csv(out / "williams_trades.csv", index=False)
    pd.DataFrame(dt).to_csv(out / "double_bollinger_trades.csv", index=False)
    summary = {
        "source_table": table,
        "dates": len(dates),
        "cost_bps": args.cost_bps,
        "fallback_risk_pct": args.fallback_risk_pct,
        "max_swing_risk_pct": args.max_swing_risk_pct,
        "williams": wm,
        "double_bollinger": dm,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    # Preliminary winner: must have enough trades and positive expectancy/PF before return comparison.
    def eligible(m):
        return m["trades"] >= 20 and m["pf"] > 1.0 and m["net_pct"] > 0.0
    if eligible(wm) and not eligible(dm):
        winner = "WILLIAMS"
    elif eligible(dm) and not eligible(wm):
        winner = "DOUBLE_BOLLINGER"
    elif eligible(wm) and eligible(dm):
        # Prefer PF first; use net return and lower drawdown only as tie-breakers.
        wk = (wm["pf"], wm["net_pct"], wm["max_dd_pct"])
        dk = (dm["pf"], dm["net_pct"], dm["max_dd_pct"])
        winner = "WILLIAMS" if wk > dk else ("DOUBLE_BOLLINGER" if dk > wk else "TIE")
    else:
        winner = "NONE_CORE_NOT_READY"
    print("WINNER_PRELIMINARY=", winner)
    print("OUTPUT_DIR=", out)
    print("NEXT=DO_NOT_TUNE_CORE_FROM_METRICS_ALONE; INSPECT_ENTRY_LOCATIONS_MFE_MAE_AND_INVARIANTS")


if __name__ == "__main__":
    main()
