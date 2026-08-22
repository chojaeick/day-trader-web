#!/usr/bin/env python3
"""
DAY TRADER V4 — DRSI + MACD 10/20/70 BASELINE V1

Purpose
-------
Explicit first baseline for the user's requested trader method:
ONLY Dynamic RSI + MACD decide staged entry/exit.

No new market data is downloaded.
Uses existing /tmp/fast_replay_cache/*.csv.

Important
---------
The historical project did NOT contain a real Dynamic-RSI or MACD implementation.
Therefore this file defines a transparent baseline interpretation and labels it
DRSI_MACD_BASELINE_V1. It is not claimed to reproduce the original trader's
proprietary formula.

Indicators
----------
MACD: standard EMA(12) - EMA(26), signal EMA(9), histogram.

Dynamic RSI:
- base RSI = Wilder RSI(14)
- adaptive center = rolling 20-bar mean of RSI
- adaptive dispersion = rolling 20-bar std of RSI
- lower band = center - 0.75 * std
- upper band = center + 0.75 * std

This makes the RSI trigger relative to its recent regime rather than fixed 30/70.

Staged position
---------------
10% PROBE:
  RSI crosses upward through dynamic lower band
  AND MACD histogram is rising for 2 bars.

+20% CONFIRM (total 30%):
  RSI > dynamic center
  AND MACD line > signal line
  AND MACD histogram > 0.

+70% FULL (total 100%):
  RSI > dynamic upper band
  AND MACD > 0
  AND histogram > 0
  AND histogram >= previous histogram.

Exit
----
Stage-down, using ONLY DRSI + MACD:
- FULL -> 30% when RSI crosses below upper band OR histogram turns down below 0.
- 30% -> 10% when RSI < center AND MACD < signal.
- 10% -> 0% when RSI crosses below lower band AND MACD < signal.
- hard signal exit to 0% if MACD < 0 AND RSI < lower band.

Execution
---------
Signals are calculated from completed bar i.
Position changes execute at next bar open (causal, no look-ahead).

Cost
----
Round-trip cost stress is approximated through turnover:
each position change incurs one-way cost = COST_RT / 2 * abs(delta exposure).

Default COST_RT = 0.20%.

Outputs
-------
/tmp/drsi_macd_baseline_v1_trades.csv
/tmp/drsi_macd_baseline_v1_daily.csv
/tmp/drsi_macd_baseline_v1.txt
"""

from pathlib import Path
import glob
import os
import math
import pandas as pd
import numpy as np

CACHE_GLOB = "/tmp/fast_replay_cache/*.csv"
COST_RT = 0.20  # percent on 100% capital, round-trip
ONE_WAY = COST_RT / 2.0

# Restrict to regular session using cache time. Existing cache is assumed ET.
START_TIME = "09:30"
END_TIME = "16:00"

MIN_BARS = 60

def wilder_rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder smoothing = alpha 1/n
    ag = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

    rs = ag / al.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(100.0)
    rsi[(ag == 0) & (al == 0)] = 50.0
    return rsi

def add_indicators(x):
    x = x.copy()

    close = pd.to_numeric(x["close"], errors="coerce")
    x["rsi"] = wilder_rsi(close, 14)

    x["drsi_mid"] = x["rsi"].rolling(20, min_periods=20).mean()
    x["drsi_std"] = x["rsi"].rolling(20, min_periods=20).std(ddof=0)
    x["drsi_low"] = x["drsi_mid"] - 0.75 * x["drsi_std"]
    x["drsi_high"] = x["drsi_mid"] + 0.75 * x["drsi_std"]

    e12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    e26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    x["macd"] = e12 - e26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]

    return x

def cross_up(a0, a1, b0, b1):
    return pd.notna(a0) and pd.notna(a1) and pd.notna(b0) and pd.notna(b1) and a0 <= b0 and a1 > b1

def cross_down(a0, a1, b0, b1):
    return pd.notna(a0) and pd.notna(a1) and pd.notna(b0) and pd.notna(b1) and a0 >= b0 and a1 < b1

def desired_exposure(x, i, cur):
    """
    Returns desired exposure percentage: 0 / 10 / 30 / 100.
    Uses completed bar i only.
    """
    r = x.iloc[i]
    p = x.iloc[i-1]
    p2 = x.iloc[i-2]

    vals = [
        r.rsi, r.drsi_low, r.drsi_mid, r.drsi_high,
        r.macd, r.macd_signal, r.macd_hist,
        p.rsi, p.drsi_low, p.drsi_high, p.macd_hist,
        p2.macd_hist
    ]
    if any(pd.isna(v) for v in vals):
        return cur, "WARMUP"

    probe = (
        cross_up(p.rsi, r.rsi, p.drsi_low, r.drsi_low)
        and r.macd_hist > p.macd_hist > p2.macd_hist
    )

    confirm = (
        r.rsi > r.drsi_mid
        and r.macd > r.macd_signal
        and r.macd_hist > 0
    )

    full = (
        r.rsi > r.drsi_high
        and r.macd > 0
        and r.macd_hist > 0
        and r.macd_hist >= p.macd_hist
    )

    full_reduce = (
        cross_down(p.rsi, r.rsi, p.drsi_high, r.drsi_high)
        or (r.macd_hist < 0 and r.macd_hist < p.macd_hist)
    )

    confirm_reduce = (
        r.rsi < r.drsi_mid
        and r.macd < r.macd_signal
    )

    exit_all = (
        cross_down(p.rsi, r.rsi, p.drsi_low, r.drsi_low)
        and r.macd < r.macd_signal
    ) or (
        r.macd < 0 and r.rsi < r.drsi_low
    )

    # exits first
    if exit_all:
        return 0, "EXIT_ALL"

    if cur >= 100 and full_reduce:
        return 30, "FULL_TO_30"

    if cur >= 30 and confirm_reduce:
        return 10, "30_TO_10"

    # entries/adds
    if cur == 0 and probe:
        return 10, "PROBE_10"

    if cur <= 10 and confirm:
        return 30, "CONFIRM_30"

    if cur <= 30 and full:
        return 100, "FULL_100"

    return cur, "HOLD"

def load_cache(path):
    x = pd.read_csv(path)

    needed = {"time", "open", "close"}
    if not needed.issubset(x.columns):
        return None

    x["time"] = x["time"].astype(str).str[:5]
    x = x[(x["time"] >= START_TIME) & (x["time"] <= END_TIME)].copy()
    x = x.reset_index(drop=True)

    for c in ["open","close"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")

    x = x.dropna(subset=["open","close"]).reset_index(drop=True)

    if len(x) < MIN_BARS:
        return None

    return add_indicators(x)

def simulate_case(path):
    case = os.path.basename(path).replace(".csv","")
    if "_" not in case:
        return [], None

    symbol, date = case.split("_", 1)
    x = load_cache(path)
    if x is None or len(x) < 40:
        return [], None

    exposure = 0
    pending_exposure = None
    pending_reason = None

    pnl = 0.0
    gross = 0.0
    cost = 0.0
    turnover = 0.0
    events = []

    max_expo = 0
    start_i = 35

    for i in range(start_i, len(x)-1):
        # execute prior signal at current open
        if pending_exposure is not None and pending_exposure != exposure:
            delta = pending_exposure - exposure
            trade_cost = abs(delta) / 100.0 * ONE_WAY
            cost += trade_cost
            turnover += abs(delta)

            events.append({
                "CASE": case,
                "SYMBOL": symbol,
                "DATE": date,
                "TIME": x.iloc[i]["time"],
                "ACTION": pending_reason,
                "FROM_EXPO": exposure,
                "TO_EXPO": pending_exposure,
                "PRICE": float(x.iloc[i]["open"]),
                "RSI": float(x.iloc[i-1]["rsi"]) if pd.notna(x.iloc[i-1]["rsi"]) else np.nan,
                "DRSI_LOW": float(x.iloc[i-1]["drsi_low"]) if pd.notna(x.iloc[i-1]["drsi_low"]) else np.nan,
                "DRSI_MID": float(x.iloc[i-1]["drsi_mid"]) if pd.notna(x.iloc[i-1]["drsi_mid"]) else np.nan,
                "DRSI_HIGH": float(x.iloc[i-1]["drsi_high"]) if pd.notna(x.iloc[i-1]["drsi_high"]) else np.nan,
                "MACD": float(x.iloc[i-1]["macd"]) if pd.notna(x.iloc[i-1]["macd"]) else np.nan,
                "MACD_SIGNAL": float(x.iloc[i-1]["macd_signal"]) if pd.notna(x.iloc[i-1]["macd_signal"]) else np.nan,
                "MACD_HIST": float(x.iloc[i-1]["macd_hist"]) if pd.notna(x.iloc[i-1]["macd_hist"]) else np.nan,
            })
            exposure = pending_exposure
            max_expo = max(max_expo, exposure)

        pending_exposure = None
        pending_reason = None

        # earn current bar open->next open on current exposure
        o0 = float(x.iloc[i]["open"])
        o1 = float(x.iloc[i+1]["open"])
        if o0 > 0:
            r = (o1 / o0 - 1) * 100.0
            gp = r * exposure / 100.0
            gross += gp
            pnl += gp

        desired, reason = desired_exposure(x, i, exposure)
        if desired != exposure:
            pending_exposure = desired
            pending_reason = reason

    # final forced close at last bar open
    if exposure != 0:
        trade_cost = exposure / 100.0 * ONE_WAY
        cost += trade_cost
        turnover += exposure
        events.append({
            "CASE": case,
            "SYMBOL": symbol,
            "DATE": date,
            "TIME": x.iloc[-1]["time"],
            "ACTION": "EOD_FLAT",
            "FROM_EXPO": exposure,
            "TO_EXPO": 0,
            "PRICE": float(x.iloc[-1]["open"]),
            "RSI": float(x.iloc[-2]["rsi"]) if pd.notna(x.iloc[-2]["rsi"]) else np.nan,
            "DRSI_LOW": float(x.iloc[-2]["drsi_low"]) if pd.notna(x.iloc[-2]["drsi_low"]) else np.nan,
            "DRSI_MID": float(x.iloc[-2]["drsi_mid"]) if pd.notna(x.iloc[-2]["drsi_mid"]) else np.nan,
            "DRSI_HIGH": float(x.iloc[-2]["drsi_high"]) if pd.notna(x.iloc[-2]["drsi_high"]) else np.nan,
            "MACD": float(x.iloc[-2]["macd"]) if pd.notna(x.iloc[-2]["macd"]) else np.nan,
            "MACD_SIGNAL": float(x.iloc[-2]["macd_signal"]) if pd.notna(x.iloc[-2]["macd_signal"]) else np.nan,
            "MACD_HIST": float(x.iloc[-2]["macd_hist"]) if pd.notna(x.iloc[-2]["macd_hist"]) else np.nan,
        })

    net = gross - cost

    summary = {
        "CASE": case,
        "SYMBOL": symbol,
        "DATE": date,
        "GROSS": gross,
        "COST": cost,
        "NET": net,
        "TURNOVER_PCT": turnover,
        "EVENTS": len(events),
        "MAX_EXPO": max_expo,
    }

    return events, summary

def metrics(df):
    if len(df) == 0:
        return {}

    wins = df[df.NET > 0]
    losses = df[df.NET <= 0]
    gp = wins.NET.sum()
    gl = -losses.NET.sum()
    pf = gp / gl if gl > 0 else math.inf

    return {
        "CASES": len(df),
        "NET_SUM": df.NET.sum(),
        "AVG_CASE_NET": df.NET.mean(),
        "WIN_CASE_RATE": (df.NET > 0).mean() * 100,
        "PF": pf,
        "BEST_CASE": df.NET.max(),
        "WORST_CASE": df.NET.min(),
        "AVG_TURNOVER": df.TURNOVER_PCT.mean(),
        "FULL_CASES": int((df.MAX_EXPO >= 100).sum()),
    }

def main():
    paths = sorted(glob.glob(CACHE_GLOB))

    all_events = []
    summaries = []

    for p in paths:
        ev, sm = simulate_case(p)
        all_events.extend(ev)
        if sm:
            summaries.append(sm)

    evdf = pd.DataFrame(all_events)
    sdf = pd.DataFrame(summaries)

    evdf.to_csv("/tmp/drsi_macd_baseline_v1_trades.csv", index=False)
    sdf.to_csv("/tmp/drsi_macd_baseline_v1_daily.csv", index=False)

    out = []
    add = out.append

    add("===== DRSI + MACD 10/20/70 BASELINE V1 =====")
    add(f"CACHE_FILES {len(paths)}")
    add(f"VALID_CASES {len(sdf)}")
    add(f"COST_RT {COST_RT:.2f}%")
    add("INDICATORS ONLY: Dynamic RSI + MACD")
    add("NO DOWNLOAD")
    add("")

    m = metrics(sdf)
    for k,v in m.items():
        if isinstance(v,float):
            add(f"{k} {v:+.3f}" if k not in {"WIN_CASE_RATE","PF"} else f"{k} {v:.3f}")
        else:
            add(f"{k} {v}")

    if len(sdf):
        add("")
        add("===== BY DATE =====")
        bd = sdf.groupby("DATE").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN=("NET", lambda x:(x>0).mean()*100),
        ).round(3)
        add(bd.to_string())

        add("")
        add("===== BY SYMBOL =====")
        bs = sdf.groupby("SYMBOL").agg(
            N=("NET","size"),
            NET=("NET","sum"),
            AVG=("NET","mean"),
            WIN=("NET", lambda x:(x>0).mean()*100),
        ).round(3).sort_values("NET", ascending=False)
        add(bs.to_string())

        add("")
        add("===== LEAVE-ONE-DATE-OUT =====")
        rows=[]
        for d in sorted(sdf.DATE.astype(str).unique()):
            r=sdf[sdf.DATE.astype(str)!=d]
            rows.append((d, len(r), r.NET.sum()))
        loo=pd.DataFrame(rows, columns=["EXCLUDED_DATE","N","NET_REMAINING"])
        add(loo.round(3).to_string(index=False))
        add(f"LOO_MIN_NET {loo.NET_REMAINING.min():+.3f}%")
        add(f"LOO_POS_RATE {(loo.NET_REMAINING>0).mean()*100:.1f}%")

    add("")
    add("===== INTERPRETATION =====")
    add("This is BASELINE V1, not a tuned production engine.")
    add("If net expectancy is poor, do not parameter-sweep immediately.")
    add("First inspect whether the original trader's exact Dynamic RSI definition differs.")
    add("If promising, next compare staged logic / exits with fixed indicators.")

    Path("/tmp/drsi_macd_baseline_v1.txt").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))

if __name__ == "__main__":
    main()
