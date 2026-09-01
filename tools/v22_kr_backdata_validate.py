from __future__ import annotations

"""Causal backdata validation for the live KR Engine5 V22 entry authority.

Uses the retained SQLite tick archive, builds completed 1-minute KR regular-session
bars, calls live_server.engine5_v22_live_kr.evaluate_entry without reimplementing
strategy rules, and measures next-bar-open forward outcomes. This isolates entry
quality from exit-policy changes.
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from live_server.config import Settings
from live_server.engine5_v22_live_kr import evaluate_entry

KST = ZoneInfo("Asia/Seoul")
HORIZONS = (5, 10, 20, 30)


def _bars_raw(bars: pd.DataFrame, end_idx: int) -> list[dict]:
    q = bars.iloc[: end_idx + 1]
    return [
        {
            "time": pd.Timestamp(r.time).strftime("%Y%m%d%H%M%S"),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }
        for r in q.itertuples(index=False)
    ]


def _load_ticks(db_path: str, days: int, symbols: list[str] | None) -> pd.DataFrame:
    if not Path(db_path).exists():
        raise FileNotFoundError(db_path)
    where = ["symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"]
    params: list[object] = []
    if symbols:
        where.append("symbol IN (%s)" % ",".join("?" for _ in symbols))
        params.extend(symbols)
    if days > 0:
        where.append("datetime(ts) >= datetime('now', ?)")
        params.append(f"-{days} days")
    sql = (
        "SELECT symbol,price,qty,cum_volume,ts FROM ticks WHERE "
        + " AND ".join(where)
        + " ORDER BY symbol,ts"
    )
    with sqlite3.connect(db_path) as con:
        x = pd.read_sql_query(sql, con, params=params)
    if x.empty:
        return x
    x["symbol"] = x["symbol"].astype(str).str.replace("A", "", regex=False).str.zfill(6)
    x["price"] = pd.to_numeric(x["price"], errors="coerce")
    x["qty"] = pd.to_numeric(x["qty"], errors="coerce").fillna(0.0).abs()
    x["cum_volume"] = pd.to_numeric(x["cum_volume"], errors="coerce").fillna(0.0)
    x["ts"] = pd.to_datetime(x["ts"], utc=True, errors="coerce")
    x = x.dropna(subset=["ts", "price"])
    x = x[x["price"] > 0].copy()
    x["kst"] = x["ts"].dt.tz_convert(KST)
    x["trade_date"] = x["kst"].dt.strftime("%Y-%m-%d")
    minute = x["kst"].dt.hour * 60 + x["kst"].dt.minute
    return x[(minute >= 9 * 60) & (minute <= 15 * 60 + 30)].copy()


def _to_1m(day_ticks: pd.DataFrame) -> pd.DataFrame:
    x = day_ticks.sort_values("kst").set_index("kst")
    ohlc = x["price"].resample("1min").ohlc()
    qty = x["qty"].resample("1min").sum().fillna(0.0)
    cum_last = x["cum_volume"].resample("1min").last()
    cum_delta = cum_last.diff().clip(lower=0).fillna(0.0)
    volume = qty.where(qty > 0, cum_delta)
    b = ohlc.join(volume.rename("volume")).dropna(subset=["close"]).reset_index()
    b["time"] = b["kst"].dt.tz_localize(None)
    return b[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _f(v):
    try:
        z = float(v)
        return z if np.isfinite(z) else np.nan
    except Exception:
        return np.nan


def _signal_row(symbol: str, day: str, bars: pd.DataFrame, i: int, d: dict) -> dict | None:
    if i + 1 >= len(bars):
        return None
    entry = float(bars.iloc[i + 1]["open"])
    if entry <= 0:
        return None
    out = {
        "trade_date": day,
        "symbol": symbol,
        "signal_time": str(bars.iloc[i]["time"]),
        "entry_time": str(bars.iloc[i + 1]["time"]),
        "entry_price": entry,
        "timing": d.get("timing"),
        "reason": d.get("reason"),
        "score": _f(d.get("score")),
        "effective_score": _f(d.get("effective_score")),
        "base_score": _f(d.get("base_score")),
        "score_trend": _f(d.get("score_trend")),
        "score_macd": _f(d.get("score_macd")),
        "score_rsi_position": _f(d.get("score_rsi_position")),
        "score_rsi_slope": _f(d.get("score_rsi_slope")),
        "score_volume": _f(d.get("score_volume")),
        "score_outer_expand": _f(d.get("score_outer_expand")),
        "score_mid_momentum": _f(d.get("score_mid_momentum")),
        "reversal_exception": bool(d.get("reversal_exception")),
        "macd_angle_diff": _f(d.get("macd_angle_diff")),
        "rsi_delta_3m": _f(d.get("rsi_delta_3m")),
        "band_r": _f(d.get("band_r")),
    }
    for h in HORIZONS:
        j = min(i + 1 + h, len(bars) - 1)
        px = float(bars.iloc[j]["close"])
        out[f"ret_{h}m_pct"] = (px / entry - 1.0) * 100.0
    end = min(i + 1 + 30, len(bars) - 1)
    future = bars.iloc[i + 1 : end + 1]
    out["mfe_30m_pct"] = (float(future["high"].max()) / entry - 1.0) * 100.0
    out["mae_30m_pct"] = (float(future["low"].min()) / entry - 1.0) * 100.0
    r = out["band_r"]
    if np.isfinite(r) and r > 0:
        out["risk_r_pct"] = r / entry * 100.0
        out["hit_plus_2r_30m"] = bool(float(future["high"].max()) >= entry + 2.0 * r)
        out["hit_minus_1r_30m"] = bool(float(future["low"].min()) <= entry - r)
    else:
        out["risk_r_pct"] = np.nan
        out["hit_plus_2r_30m"] = False
        out["hit_minus_1r_30m"] = False
    return out


def _scan_symbol_day(symbol: str, day: str, bars: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    prior_enter = False
    for i in range(29, len(bars) - 1):
        bar = bars.iloc[i]
        d = evaluate_entry(
            {
                "symbol": symbol,
                "price": float(bar["close"]),
                "bars_raw": _bars_raw(bars, i),
            }
        )
        enter = bool(d.get("enter"))
        # Count the start of an entry episode once; consecutive TRUE minutes are not independent trades.
        if enter and not prior_enter:
            r = _signal_row(symbol, day, bars, i, d)
            if r:
                rows.append(r)
        prior_enter = enter
    return rows


def _summary(signals: pd.DataFrame, ticks: pd.DataFrame) -> dict:
    base = {
        "signals": int(len(signals)),
        "symbols": int(ticks["symbol"].nunique()) if not ticks.empty else 0,
        "days": int(ticks["trade_date"].nunique()) if not ticks.empty else 0,
        "data_start_kst": str(ticks["kst"].min()) if not ticks.empty else None,
        "data_end_kst": str(ticks["kst"].max()) if not ticks.empty else None,
    }
    if signals.empty:
        return base
    for h in HORIZONS:
        r = signals[f"ret_{h}m_pct"].astype(float)
        base[f"avg_ret_{h}m_pct"] = round(float(r.mean()), 4)
        base[f"median_ret_{h}m_pct"] = round(float(r.median()), 4)
        base[f"positive_{h}m_rate_pct"] = round(float((r > 0).mean() * 100.0), 2)
    base["avg_mfe_30m_pct"] = round(float(signals["mfe_30m_pct"].mean()), 4)
    base["avg_mae_30m_pct"] = round(float(signals["mae_30m_pct"].mean()), 4)
    base["plus_2r_30m_rate_pct"] = round(float(signals["hit_plus_2r_30m"].mean() * 100.0), 2)
    base["minus_1r_30m_rate_pct"] = round(float(signals["hit_minus_1r_30m"].mean() * 100.0), 2)
    base["reversal_exception_signals"] = int(signals["reversal_exception"].sum())
    if base["reversal_exception_signals"]:
        q = signals[signals["reversal_exception"]]
        base["reversal_avg_ret_10m_pct"] = round(float(q["ret_10m_pct"].mean()), 4)
        base["reversal_positive_10m_rate_pct"] = round(float((q["ret_10m_pct"] > 0).mean() * 100.0), 2)
    return base


def main() -> None:
    p = argparse.ArgumentParser(description="Validate KR V22 entry engine on retained tick backdata")
    p.add_argument("--db", default=Settings().db_path)
    p.add_argument("--days", type=int, default=0, help="0 = all retained history")
    p.add_argument("--symbols", default="", help="comma-separated six-digit KR symbols")
    p.add_argument("--out", default="backtest_results")
    a = p.parse_args()
    symbols = [s.strip().replace("A", "").zfill(6) for s in a.symbols.split(",") if s.strip()] or None
    ticks = _load_ticks(a.db, a.days, symbols)
    if ticks.empty:
        print(json.dumps({"ok": False, "reason": "NO_KR_TICKS", "db": a.db}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    found: list[dict] = []
    groups = list(ticks.groupby(["symbol", "trade_date"], sort=True))
    for n, ((symbol, day), g) in enumerate(groups, 1):
        bars = _to_1m(g)
        if len(bars) >= 31:
            found.extend(_scan_symbol_day(symbol, day, bars))
        if n % 50 == 0 or n == len(groups):
            print(f"progress={n}/{len(groups)} signals={len(found)}", flush=True)

    signals = pd.DataFrame(found)
    result = {
        "ok": True,
        "engine": "ENGINE5_V22_KR_LIVE",
        "validation": "CAUSAL_ENTRY_FORWARD_RETURN",
        "fill": "NEXT_AVAILABLE_1M_OPEN",
        "summary": _summary(signals, ticks),
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jpath = out / f"v22_kr_backdata_summary_{stamp}.json"
    cpath = out / f"v22_kr_backdata_signals_{stamp}.csv"
    jpath.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if not signals.empty:
        signals.to_csv(cpath, index=False, encoding="utf-8-sig")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"summary_file={jpath}")
    if not signals.empty:
        print(f"signals_file={cpath}")


if __name__ == "__main__":
    main()
