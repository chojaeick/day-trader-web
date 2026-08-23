#!/usr/bin/env python3
"""MA20_SCALP v0.2 - robustness probe for GAP_REVERSION.

Purpose
- Preserve v0.1 GAP_REVERSION architecture.
- Do NOT tune on symbol identity.
- Measure sensitivity to gap/recovery/cost/stop assumptions.
- Report date and symbol concentration so tiny-sample false confidence is obvious.

Read only. No DB writes. No auto orders.
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
    symbol: str
    date: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    gross_pct: float
    net_pct: float
    reason: str
    gap_pct: float
    recovery: float
    cost: float


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="AMD,ARM,AVGO,INTC,NVDA,SMCI,TSM")
    p.add_argument("--min-date", default="20260722")
    p.add_argument("--max-date", default="99999999")
    p.add_argument("--max-hold", type=int, default=30)
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
    z["gap_pct"] = (z["ma20"] - z["close"]) / z["ma20"] * 100.0
    return z


def run_case(sym, date, z, gap_thr, recovery, min_gross, cost, stop_extra, max_hold):
    out=[]
    i=21
    while i < len(z)-1:
        ma=z.loc[i,"ma20"]
        if pd.isna(ma):
            i+=1; continue
        gap=float(z.loc[i,"gap_pct"])
        prev_gap=float(z.loc[i-1,"gap_pct"])
        px=float(z.loc[i,"close"])
        target=px+(float(ma)-px)*recovery
        target_gross=(target/px-1.0)*100.0
        contracting=gap < prev_gap
        if gap >= gap_thr and contracting and target_gross >= min_gross:
            entry_gap=gap
            end=min(len(z)-1,i+max_hold)
            filled=False
            for j in range(i+1,end+1):
                if float(z.loc[j,"high"]) >= target:
                    gross=(target/px-1.0)*100.0
                    out.append(Trade(sym,str(date),str(z.loc[i,"et_time"]),str(z.loc[j,"et_time"]),px,target,gross,gross-cost,"TARGET",gap_thr,recovery,cost))
                    i=j+1; filled=True; break
                if float(z.loc[j,"gap_pct"]) >= entry_gap + stop_extra:
                    x=float(z.loc[j,"close"])
                    gross=(x/px-1.0)*100.0
                    out.append(Trade(sym,str(date),str(z.loc[i,"et_time"]),str(z.loc[j,"et_time"]),px,x,gross,gross-cost,"GAP_STOP",gap_thr,recovery,cost))
                    i=j+1; filled=True; break
            if not filled:
                x=float(z.loc[end,"close"])
                gross=(x/px-1.0)*100.0
                out.append(Trade(sym,str(date),str(z.loc[i,"et_time"]),str(z.loc[end,"et_time"]),px,x,gross,gross-cost,"TIME",gap_thr,recovery,cost))
                i=end+1
        else:
            i+=1
    return out


def metrics(trades):
    if not trades:
        return dict(TRADES=0,NET=0.0,AVG=0.0,WIN_RATE=0.0,PF=0.0,POS_DATES=0,DATES=0,WORST_DATE=0.0,TOP_SYMBOL_SHARE=0.0)
    x=pd.DataFrame(asdict(t) for t in trades)
    pos=x.loc[x.net_pct>0,"net_pct"].sum()
    neg=-x.loc[x.net_pct<0,"net_pct"].sum()
    pf=pos/neg if neg>0 else float("inf")
    bydate=x.groupby("date").net_pct.sum()
    bysym=x.groupby("symbol").net_pct.sum()
    top_share=(bysym.max()/x.net_pct.sum()*100.0) if x.net_pct.sum()>0 else 0.0
    return dict(
        TRADES=len(x), NET=x.net_pct.sum(), AVG=x.net_pct.mean(),
        WIN_RATE=(x.net_pct>0).mean()*100.0, PF=pf,
        POS_DATES=int((bydate>0).sum()), DATES=int(len(bydate)),
        WORST_DATE=float(bydate.min()), TOP_SYMBOL_SHARE=float(top_share),
    )


def main():
    a=parse_args()
    symbols=[s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    sessions=load_sessions(a.db,symbols,a.min_date,a.max_date)
    prepared=[(s,d,add_features(z)) for s,d,z in sessions if len(z)>=40]

    gap_grid=[1.75,2.00,2.25,2.50,3.00]
    recovery_grid=[0.20,0.25,0.33]
    cost_grid=[0.20,0.25,0.30]
    stop_grid=[0.50,0.75,1.00]

    rows=[]
    for gap in gap_grid:
        for recovery in recovery_grid:
            for cost in cost_grid:
                for stop in stop_grid:
                    min_gross=0.50
                    tt=[]
                    for sym,date,z in prepared:
                        tt += run_case(sym,date,z,gap,recovery,min_gross,cost,stop,a.max_hold)
                    m=metrics(tt)
                    rows.append(dict(GAP=gap,RECOVERY=recovery,COST=cost,STOP_EXTRA=stop,**m))

    r=pd.DataFrame(rows)
    print("===== MA20 SCALP v0.2 ROBUSTNESS =====")
    print("READ ONLY / DB WRITE NONE / NO AUTO ORDER")
    print("SESSIONS",len(prepared))
    print("GRID_CASES",len(r))

    print("\n===== BASELINE =====")
    b=r[(r.GAP==2.00)&(r.RECOVERY==0.25)&(r.COST==0.20)&(r.STOP_EXTRA==0.75)]
    print(b.round(3).to_string(index=False))

    print("\n===== COST SWEEP @ GAP2 REC25 STOP0.75 =====")
    z=r[(r.GAP==2.00)&(r.RECOVERY==0.25)&(r.STOP_EXTRA==0.75)]
    print(z[["COST","TRADES","NET","AVG","WIN_RATE","PF","POS_DATES","DATES","WORST_DATE","TOP_SYMBOL_SHARE"]].round(3).to_string(index=False))

    print("\n===== GAP SWEEP @ REC25 COST0.20 STOP0.75 =====")
    z=r[(r.RECOVERY==0.25)&(r.COST==0.20)&(r.STOP_EXTRA==0.75)]
    print(z[["GAP","TRADES","NET","AVG","WIN_RATE","PF","POS_DATES","DATES","WORST_DATE","TOP_SYMBOL_SHARE"]].round(3).to_string(index=False))

    print("\n===== ROBUST POSITIVE CASES =====")
    pos=r[(r.NET>0)&(r.PF>1.0)&(r.TRADES>=5)].copy()
    print("POSITIVE_CASES",len(pos),"/",len(r))
    if len(pos):
        print(pos.sort_values(["COST","NET"],ascending=[True,False]).head(20).round(3).to_string(index=False))

    print("\n===== DECISION SUPPORT =====")
    base=b.iloc[0] if len(b) else None
    if base is None:
        print("BASELINE_FOUND False")
    else:
        print("BASELINE_FOUND True")
        print("BASE_TRADES",int(base.TRADES))
        print("BASE_NET",f"{base.NET:+.3f}%")
        print("BASE_POS_DATES",f"{int(base.POS_DATES)}/{int(base.DATES)}")
        print("BASE_TOP_SYMBOL_SHARE",f"{base.TOP_SYMBOL_SHARE:.1f}%")
        print("SAMPLE_WARNING",bool(base.TRADES < 30))
        print("CONCENTRATION_WARNING",bool(base.TOP_SYMBOL_SHARE > 60.0))
        print("NEXT: if baseline survives cost/gap perturbation, expand to a fresh training split; do not promote from 8 trades alone.")

if __name__=="__main__":
    main()
