#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from live_server.strategy_core_v1 import Action, PositionState
from live_server.strategy_pair_selector_v1 import choose_single_entry
from live_server.williams_clean_v1 import CleanWilliamsV1, WilliamsConfig
from live_server.double_bollinger_v1 import DoubleBollingerV1, DoubleBollingerConfig


SYMBOLS = ("SOXL", "SOXS")
ET = "America/New_York"


def qident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def find_market_table(con: sqlite3.Connection):
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    sym_names = ["symbol", "sym", "code", "stk_cd"]
    time_names = ["ts", "time", "timestamp", "datetime", "created_at", "updated_at"]
    price_names = ["price", "last", "last_price", "cur_prc", "current_price"]
    volume_names = ["volume", "qty", "size", "trade_qty", "trde_qty"]
    preferred = []
    for t in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({qident(t)})")]
        low = {c.lower(): c for c in cols}
        sym = next((low[x] for x in sym_names if x in low), None)
        tm = next((low[x] for x in time_names if x in low), None)
        has_ohlc = all(x in low for x in ("open", "high", "low", "close"))
        price = next((low[x] for x in price_names if x in low), None)
        vol = next((low[x] for x in volume_names if x in low), None)
        if sym and tm and (has_ohlc or price):
            score = 0
            tl = t.lower()
            if "tick" in tl: score += 5
            if "bar" in tl or "minute" in tl: score += 4
            if has_ohlc: score += 3
            preferred.append((score, t, sym, tm, has_ohlc, price, vol, low))
    if not preferred:
        raise RuntimeError("no market table with symbol/time/price-or-OHLC found")
    preferred.sort(reverse=True, key=lambda z: z[0])
    return preferred[0]


def parse_ts(s: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(s):
        v = pd.to_numeric(s, errors="coerce")
        med = float(v.dropna().abs().median()) if not v.dropna().empty else 0.0
        unit = "ns" if med > 1e17 else ("ms" if med > 1e11 else "s")
        return pd.to_datetime(v, unit=unit, errors="coerce", utc=True)
    st = s.astype(str).str.strip()
    out = pd.to_datetime(st, errors="coerce", utc=True)
    if out.notna().mean() < 0.5:
        out = pd.to_datetime(st.str[:14], format="%Y%m%d%H%M%S", errors="coerce", utc=True)
    return out


def load_1m_bars(db: str, max_days: int) -> Tuple[pd.DataFrame, str]:
    con = sqlite3.connect(db)
    try:
        score, table, symc, tmc, has_ohlc, pricec, volc, low = find_market_table(con)
        fields = [symc, tmc]
        if has_ohlc:
            fields += [low["open"], low["high"], low["low"], low["close"]]
        else:
            fields += [pricec]
        if volc:
            fields += [volc]
        fields = list(dict.fromkeys(fields))
        sel = ",".join(qident(x) for x in fields)
        sql = f"SELECT {sel} FROM {qident(table)} WHERE {qident(symc)} IN (?,?) ORDER BY {qident(tmc)}"
        df = pd.read_sql_query(sql, con, params=SYMBOLS)
    finally:
        con.close()

    ren = {symc: "symbol", tmc: "time"}
    if has_ohlc:
        ren.update({low["open"]:"open", low["high"]:"high", low["low"]:"low", low["close"]:"close"})
    else:
        ren[pricec] = "price"
    if volc:
        ren[volc] = "volume"
    df = df.rename(columns=ren)
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df = df[df["symbol"].isin(SYMBOLS)].copy()
    df["_utc"] = parse_ts(df["time"])
    df = df.dropna(subset=["_utc"]).sort_values(["_utc", "symbol"])
    if df.empty:
        raise RuntimeError("SOXL/SOXS rows not found in selected market table")

    if max_days > 0:
        cutoff = df["_utc"].max() - pd.Timedelta(days=max_days + 7)
        df = df[df["_utc"] >= cutoff].copy()

    if has_ohlc:
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 1.0), errors="coerce").fillna(0.0)
        # Normalize any higher-frequency OHLC rows into 1-minute bars.
        df["minute"] = df["_utc"].dt.floor("min")
        bars = df.groupby(["symbol", "minute"], as_index=False).agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
        )
    else:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 1.0), errors="coerce").fillna(1.0)
        df = df.dropna(subset=["price"])
        df["minute"] = df["_utc"].dt.floor("min")
        bars = df.groupby(["symbol", "minute"], as_index=False).agg(
            open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"), volume=("volume", "sum")
        )
    bars = bars.rename(columns={"minute":"time"}).sort_values(["time","symbol"]).reset_index(drop=True)
    bars["et"] = bars["time"].dt.tz_convert(ET)
    bars["date_et"] = bars["et"].dt.date
    mins = bars["et"].dt.hour * 60 + bars["et"].dt.minute
    bars = bars[(mins >= 9*60+30) & (mins < 16*60)].copy()
    if max_days > 0 and not bars.empty:
        keep_dates = sorted(bars["date_et"].unique())[-max_days:]
        bars = bars[bars["date_et"].isin(keep_dates)].copy()
    return bars.reset_index(drop=True), table


def make_symbol_histories(bars: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    out = {}
    for sym in SYMBOLS:
        x = bars[bars["symbol"] == sym].copy().sort_values("time").reset_index(drop=True)
        x["time"] = x["time"].astype(str)
        out[sym] = x[["time","open","high","low","close","volume","et","date_et"]].copy()
    return out


def williams_daily(hist: pd.DataFrame):
    x = hist.copy()
    g = x.groupby("date_et")
    daily = g.agg(session_open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last")).reset_index()
    rows = {}
    for i in range(1, len(daily)):
        d = daily.iloc[i]
        p = daily.iloc[i-1]
        rows[d["date_et"]] = {
            "session_open": float(d["session_open"]),
            "prev_high": float(p["high"]),
            "prev_low": float(p["low"]),
        }
    return rows


def metrics(trades: List[dict]):
    if not trades:
        return {"trades":0,"wins":0,"win_rate":0.0,"net_pct":0.0,"pf":0.0,"avg_pct":0.0,"avg_mfe_pct":0.0,"avg_mae_pct":0.0,"max_dd_pct":0.0}
    r = [float(t["net_return"]) for t in trades]
    pos = sum(x for x in r if x > 0)
    neg = -sum(x for x in r if x < 0)
    equity = 1.0
    peak = 1.0
    mdd = 0.0
    for x in r:
        equity *= (1.0 + x)
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1.0)
    wins = sum(x > 0 for x in r)
    return {
        "trades": len(r),
        "wins": wins,
        "win_rate": wins / len(r),
        "net_pct": (equity - 1.0) * 100.0,
        "pf": (pos / neg) if neg > 0 else (999.0 if pos > 0 else 0.0),
        "avg_pct": sum(r) / len(r) * 100.0,
        "avg_mfe_pct": sum(float(t["mfe"]) for t in trades) / len(trades) * 100.0,
        "avg_mae_pct": sum(float(t["mae"]) for t in trades) / len(trades) * 100.0,
        "max_dd_pct": mdd * 100.0,
    }


def replay(engine_name: str, engine, histories: Dict[str,pd.DataFrame], cost_bps: float):
    # Build timestamp -> available bar index map per symbol.
    idx_by_time: Dict[pd.Timestamp, Dict[str,int]] = {}
    et_lookup = {}
    date_lookup = {}
    for sym, h in histories.items():
        for i, row in h.iterrows():
            t = pd.Timestamp(row["et"]).tz_convert("UTC")
            idx_by_time.setdefault(t, {})[sym] = i
            et_lookup[t] = pd.Timestamp(row["et"])
            date_lookup[t] = row["date_et"]
    times = sorted(idx_by_time)
    daily = {sym:williams_daily(h) for sym,h in histories.items()} if engine_name == "WILLIAMS" else {}

    state: Optional[PositionState] = None
    entry_time = None
    entry_price = None
    entry_reason = None
    mfe = 0.0
    mae = 0.0
    trades = []

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

    for t in times:
        et = et_lookup[t]
        current_date = date_lookup[t]

        if state is not None:
            sym = state.symbol
            if sym in idx_by_time[t]:
                i = idx_by_time[t][sym]
                h = histories[sym].iloc[:i+1].copy()
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
                    # V1 historical comparison keeps accounting simple: record policy diagnostics but do not split trade equity.
                    state.partial_exit(res.exit_fraction or 0.5)

        if state is None:
            candidates = []
            for sym in SYMBOLS:
                if sym not in idx_by_time[t]:
                    continue
                i = idx_by_time[t][sym]
                h = histories[sym].iloc[:i+1].copy()
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
                mfe = 0.0
                mae = 0.0

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
    ap.add_argument("--out-dir", default="/home/ubuntu/day-trader-api/validation_v240")
    args = ap.parse_args()

    print("=== V240 SOXL/SOXS TWO-ENGINE HISTORICAL REPLAY ===")
    print("SYMBOLS=SOXL,SOXS FINDER=OFF SINGLE_POSITION=YES SESSION=US_REGULAR")
    print("BACKTEST_LIVE_CORE_PARITY=SAME_ENGINE_METHODS COST_BPS=", args.cost_bps)
    bars, table = load_1m_bars(args.db, args.max_days)
    print("SOURCE_TABLE=", table, "BARS=", len(bars), "DATES=", bars["date_et"].nunique())
    print("PER_SYMBOL=", bars.groupby("symbol").size().to_dict())
    histories = make_symbol_histories(bars)

    williams = CleanWilliamsV1(WilliamsConfig())
    dbb = DoubleBollingerV1(DoubleBollingerConfig())

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
        "bars": len(bars),
        "dates": int(bars["date_et"].nunique()),
        "cost_bps": args.cost_bps,
        "williams": wm,
        "double_bollinger": dm,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    def rank(m):
        # No single magic metric: positive expectancy/PF first, then drawdown, then trade count.
        return (m["pf"] > 1.0, m["net_pct"] > 0.0, m["pf"], m["net_pct"], m["max_dd_pct"], m["trades"])
    if wm["trades"] == 0 and dm["trades"] == 0:
        winner = "NONE_ZERO_TRADES"
    elif rank(wm) > rank(dm):
        winner = "WILLIAMS"
    elif rank(dm) > rank(wm):
        winner = "DOUBLE_BOLLINGER"
    else:
        winner = "TIE"
    print("WINNER_PRELIMINARY=", winner)
    print("OUTPUT_DIR=", out)
    print("NEXT=INSPECT_MFE_MAE_AND_ENTRY_LOCATIONS_BEFORE_LIVE_MOCK")


if __name__ == "__main__":
    main()
