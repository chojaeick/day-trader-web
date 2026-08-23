#!/usr/bin/env python3
"""MA20_SCALP v0.1 - read-only 1-minute backtest.

Two independent setups are measured without changing the existing DAY TRADER engine.

A) GAP_REVERSION
   - 1m close is sufficiently below SMA20
   - the gap stops widening and begins to contract
   - only enter if recovering 25% of the entry gap implies >= min_gross return
   - target = 25% recovery toward the entry-time SMA20

B) TREND_PULLBACK
   - SMA20 slope positive
   - current swing low is above previous swing low (higher low)
   - price is near SMA20
   - first bullish close confirms rebound
   - target = recent local high; stop = current swing low

No DB writes. No auto orders. OOS-safe within each session: every decision uses only data
available at or before that bar. This is an architecture probe, not a production strategy.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "daytrader.db"


@dataclass
class Trade:
    mode: str
    symbol: str
    date: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    gross_pct: float
    net_pct: float
    reason: str


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="AMD,ARM,AVGO,INTC,NVDA,SMCI,TSM")
    p.add_argument("--min-date", default="20260722")
    p.add_argument("--max-date", default="99999999")
    p.add_argument("--gap-pct", type=float, default=2.0, help="A: minimum MA20 gap percent")
    p.add_argument("--recovery", type=float, default=0.25, help="A: fraction of entry gap recovered")
    p.add_argument("--min-gross", type=float, default=0.50, help="A: minimum target gross percent")
    p.add_argument("--cost", type=float, default=0.20, help="round-trip cost percent")
    p.add_argument("--stop-extra", type=float, default=0.75, help="A: extra gap expansion stop, percentage points")
    p.add_argument("--max-hold", type=int, default=30, help="maximum hold bars")
    p.add_argument("--pullback-band", type=float, default=0.75, help="B: abs distance from MA20 percent")
    p.add_argument("--swing", type=int, default=5, help="B: swing-low lookback half-window approximation")
    p.add_argument("--mode", choices=["A", "B", "BOTH"], default="BOTH")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def load_sessions(db, symbols, min_date, max_date):
    con = sqlite3.connect(db)
    marks = ",".join("?" for _ in symbols)
    q = f"""
      SELECT symbol, trade_date, et_time, open, high, low, close, volume
      FROM historical_minute_bars
      WHERE interval_min=1
        AND session='REGULAR'
        AND trade_date>=? AND trade_date<=?
        AND symbol IN ({marks})
      ORDER BY symbol, trade_date, et_time
    """
    df = pd.read_sql_query(q, con, params=[min_date, max_date, *symbols])
    con.close()
    if df.empty:
        return []
    return [(s, d, z.reset_index(drop=True)) for (s, d), z in df.groupby(["symbol", "trade_date"], sort=True)]


def add_features(z):
    z = z.copy()
    z["ma20"] = z["close"].rolling(20, min_periods=20).mean()
    z["ma20_slope"] = z["ma20"] - z["ma20"].shift(3)
    z["gap_pct"] = (z["ma20"] - z["close"]) / z["ma20"] * 100.0
    z["bull"] = z["close"] > z["open"]
    return z


def close_trade(mode, sym, date, z, ei, xi, cost, reason):
    e = float(z.loc[ei, "close"])
    x = float(z.loc[xi, "close"])
    gross = (x / e - 1.0) * 100.0
    return Trade(mode, sym, str(date), str(z.loc[ei, "et_time"]), str(z.loc[xi, "et_time"]), e, x, gross, gross-cost, reason)


def run_a(sym, date, z, a):
    out = []
    i = 21
    while i < len(z)-1:
        ma = z.loc[i, "ma20"]
        if pd.isna(ma):
            i += 1; continue
        gap = float(z.loc[i, "gap_pct"])
        prev_gap = float(z.loc[i-1, "gap_pct"])
        # Causal trigger: already oversold and gap has just started shrinking.
        contracting = gap < prev_gap
        px = float(z.loc[i, "close"])
        target = px + (float(ma)-px) * a.recovery
        target_gross = (target / px - 1.0) * 100.0
        if gap >= a.gap_pct and contracting and target_gross >= a.min_gross:
            entry_gap = gap
            end = min(len(z)-1, i+a.max_hold)
            xi = end; reason = "TIME"
            for j in range(i+1, end+1):
                # Entry-time MA target is fixed to avoid moving-target lookahead ambiguity.
                if float(z.loc[j, "high"]) >= target:
                    # approximate target fill rather than bar close
                    e = px; x = target
                    gross = (x/e-1.0)*100.0
                    out.append(Trade("GAP_REVERSION", sym, str(date), str(z.loc[i,"et_time"]), str(z.loc[j,"et_time"]), e, x, gross, gross-a.cost, "TARGET"))
                    xi = j; reason = None; break
                if float(z.loc[j, "gap_pct"]) >= entry_gap + a.stop_extra:
                    xi = j; reason = "GAP_STOP"; break
            if reason is not None:
                out.append(close_trade("GAP_REVERSION", sym, date, z, i, xi, a.cost, reason))
            i = xi + 1
        else:
            i += 1
    return out


def recent_swing_lows(z, i, w):
    # Strictly historical approximation: minima in two preceding, non-overlapping windows.
    if i < 2*w+1:
        return None
    prev = z.loc[i-2*w:i-w-1, "low"]
    cur = z.loc[i-w:i-1, "low"]
    if prev.empty or cur.empty:
        return None
    return float(prev.min()), float(cur.min())


def run_b(sym, date, z, a):
    out = []
    i = max(22, 2*a.swing+2)
    while i < len(z)-1:
        ma = z.loc[i, "ma20"]
        if pd.isna(ma):
            i += 1; continue
        lows = recent_swing_lows(z, i, a.swing)
        if not lows:
            i += 1; continue
        low1, low2 = lows
        px = float(z.loc[i, "close"])
        dist = abs(px/float(ma)-1.0)*100.0
        cond = (
            float(z.loc[i, "ma20_slope"]) > 0
            and low2 > low1
            and dist <= a.pullback_band
            and bool(z.loc[i, "bull"])
            and px > float(z.loc[i-1, "close"])
        )
        if not cond:
            i += 1; continue
        # target = highest high of prior 10 completed bars; stop = higher-low window minimum.
        target = float(z.loc[max(0,i-10):i-1, "high"].max())
        stop = low2
        if target <= px or stop >= px:
            i += 1; continue
        end = min(len(z)-1, i+a.max_hold)
        xi=end; reason="TIME"
        for j in range(i+1,end+1):
            if float(z.loc[j,"low"]) <= stop:
                e=px; x=stop; gross=(x/e-1)*100
                out.append(Trade("TREND_PULLBACK",sym,str(date),str(z.loc[i,"et_time"]),str(z.loc[j,"et_time"]),e,x,gross,gross-a.cost,"STOP"))
                xi=j; reason=None; break
            if float(z.loc[j,"high"]) >= target:
                e=px; x=target; gross=(x/e-1)*100
                out.append(Trade("TREND_PULLBACK",sym,str(date),str(z.loc[i,"et_time"]),str(z.loc[j,"et_time"]),e,x,gross,gross-a.cost,"TARGET"))
                xi=j; reason=None; break
        if reason is not None:
            out.append(close_trade("TREND_PULLBACK",sym,date,z,i,xi,a.cost,reason))
        i=xi+1
    return out


def summary(trades):
    if not trades:
        return None
    x = pd.DataFrame(asdict(t) for t in trades)
    wins = x.net_pct > 0
    pos = x.loc[x.net_pct>0,"net_pct"].sum()
    neg = -x.loc[x.net_pct<0,"net_pct"].sum()
    pf = pos/neg if neg>0 else float("inf")
    return {
        "TRADES": len(x),
        "NET": x.net_pct.sum(),
        "AVG": x.net_pct.mean(),
        "WIN_RATE": wins.mean()*100,
        "PF": pf,
        "POS_DATES": int((x.groupby("date").net_pct.sum()>0).sum()),
        "DATES": int(x.date.nunique()),
    }, x


def main():
    a = parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    sessions = load_sessions(a.db, symbols, a.min_date, a.max_date)
    trades=[]
    for sym,date,z0 in sessions:
        if len(z0) < 40: continue
        z=add_features(z0)
        if a.mode in ("A","BOTH"):
            trades += run_a(sym,date,z,a)
        if a.mode in ("B","BOTH"):
            trades += run_b(sym,date,z,a)

    print("===== MA20 SCALP v0.1 =====")
    print("READ ONLY / DB WRITE NONE / NO AUTO ORDER")
    print("SESSIONS", len(sessions))
    print(f"COST {a.cost:.2f}%")
    for mode in ["GAP_REVERSION","TREND_PULLBACK"]:
        tt=[t for t in trades if t.mode==mode]
        if not tt: continue
        s,x=summary(tt)
        print(f"\n===== {mode} =====")
        print("TRADES",s["TRADES"])
        print("NET",f'{s["NET"]:+.3f}%')
        print("AVG",f'{s["AVG"]:+.3f}%')
        print("WIN_RATE",f'{s["WIN_RATE"]:.1f}%')
        print("PF",f'{s["PF"]:.3f}')
        print("POS_DATES",f'{s["POS_DATES"]}/{s["DATES"]}')
        bysym=x.groupby("symbol").agg(N=("net_pct","size"),NET=("net_pct","sum"),AVG=("net_pct","mean")).sort_values("NET",ascending=False)
        print("TOP_SYMBOLS")
        print(bysym.head(5).round(3).to_string())
        print("BOTTOM_SYMBOLS")
        print(bysym.tail(5).round(3).to_string())

if __name__ == "__main__":
    main()
