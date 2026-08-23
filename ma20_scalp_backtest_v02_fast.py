#!/usr/bin/env python3
"""MA20_SCALP v0.2-fast — same GAP_REVERSION robustness probe, optimized.

Key optimization vs v0.2:
- Load DB once.
- Compute MA20/gap once per session.
- Convert each session to NumPy arrays once (no pandas .loc inside hot loops).
- Simulate each GAP/RECOVERY/STOP case once; apply COST sweep analytically afterward.
- Read only. No DB writes. No auto orders.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "daytrader.db"


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
    sessions=[]
    for (sym,date), z in df.groupby(["symbol","trade_date"], sort=True):
        z=z.reset_index(drop=True)
        if len(z)<40:
            continue
        close=z["close"].to_numpy(dtype=float)
        high=z["high"].to_numpy(dtype=float)
        times=z["et_time"].astype(str).to_numpy()
        ma=pd.Series(close).rolling(20,min_periods=20).mean().to_numpy(dtype=float)
        gap=(ma-close)/ma*100.0
        sessions.append((str(sym),str(date),times,close,high,ma,gap))
    return sessions


def run_case(session, gap_thr, recovery, min_gross, stop_extra, max_hold):
    sym,date,times,close,high,ma,gap=session
    n=len(close)
    out=[]
    i=21
    while i<n-1:
        g=gap[i]
        if not np.isfinite(g):
            i+=1; continue
        px=close[i]
        target=px+(ma[i]-px)*recovery
        target_gross=(target/px-1.0)*100.0
        if g>=gap_thr and g<gap[i-1] and target_gross>=min_gross:
            entry_gap=g
            end=min(n-1,i+max_hold)
            xi=end; exit_px=close[end]; reason="TIME"
            for j in range(i+1,end+1):
                if high[j]>=target:
                    xi=j; exit_px=target; reason="TARGET"; break
                if gap[j]>=entry_gap+stop_extra:
                    xi=j; exit_px=close[j]; reason="GAP_STOP"; break
            gross=(exit_px/px-1.0)*100.0
            out.append((sym,date,times[i],times[xi],px,exit_px,gross,reason))
            i=xi+1
        else:
            i+=1
    return out


def metrics(trades, cost):
    if not trades:
        return dict(TRADES=0,NET=0.0,AVG=0.0,WIN_RATE=0.0,PF=0.0,POS_DATES=0,DATES=0,WORST_DATE=0.0,TOP_SYMBOL_SHARE=0.0)
    syms=np.array([t[0] for t in trades],dtype=object)
    dates=np.array([t[1] for t in trades],dtype=object)
    gross=np.array([t[6] for t in trades],dtype=float)
    net=gross-cost
    pos=net[net>0].sum()
    neg=-net[net<0].sum()
    pf=pos/neg if neg>0 else float("inf")
    unique_dates=np.unique(dates)
    date_sums=np.array([net[dates==d].sum() for d in unique_dates])
    unique_syms=np.unique(syms)
    sym_sums=np.array([net[syms==s].sum() for s in unique_syms])
    total=net.sum()
    top_share=(sym_sums.max()/total*100.0) if total>0 else 0.0
    return dict(
        TRADES=len(net), NET=float(total), AVG=float(net.mean()),
        WIN_RATE=float((net>0).mean()*100.0), PF=float(pf),
        POS_DATES=int((date_sums>0).sum()), DATES=int(len(unique_dates)),
        WORST_DATE=float(date_sums.min()), TOP_SYMBOL_SHARE=float(top_share),
    )


def main():
    a=parse_args(); t0=time.time()
    symbols=[s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    sessions=load_sessions(a.db,symbols,a.min_date,a.max_date)
    print("===== MA20 SCALP v0.2-FAST =====", flush=True)
    print("READ ONLY / DB WRITE NONE / NO AUTO ORDER", flush=True)
    print("SESSIONS",len(sessions), flush=True)

    gap_grid=[1.75,2.00,2.25,2.50,3.00]
    recovery_grid=[0.20,0.25,0.33]
    cost_grid=[0.20,0.25,0.30]
    stop_grid=[0.50,0.75,1.00]
    min_gross=0.50

    # Simulate only 5*3*3 = 45 structural cases. Cost is applied afterward.
    structural={}
    total_struct=len(gap_grid)*len(recovery_grid)*len(stop_grid)
    done=0
    for gap_thr in gap_grid:
        for recovery in recovery_grid:
            for stop_extra in stop_grid:
                tt=[]
                for session in sessions:
                    tt.extend(run_case(session,gap_thr,recovery,min_gross,stop_extra,a.max_hold))
                structural[(gap_thr,recovery,stop_extra)]=tt
                done+=1
                if done%5==0 or done==total_struct:
                    print(f"PROGRESS {done}/{total_struct} elapsed={time.time()-t0:.1f}s", flush=True)

    rows=[]
    for (gap_thr,recovery,stop_extra),tt in structural.items():
        for cost in cost_grid:
            m=metrics(tt,cost)
            rows.append(dict(GAP=gap_thr,RECOVERY=recovery,COST=cost,STOP_EXTRA=stop_extra,**m))
    r=pd.DataFrame(rows)
    print("GRID_CASES",len(r), flush=True)

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
    if len(b):
        base=b.iloc[0]
        print("BASELINE_FOUND True")
        print("BASE_TRADES",int(base.TRADES))
        print("BASE_NET",f"{base.NET:+.3f}%")
        print("BASE_POS_DATES",f"{int(base.POS_DATES)}/{int(base.DATES)}")
        print("BASE_TOP_SYMBOL_SHARE",f"{base.TOP_SYMBOL_SHARE:.1f}%")
        print("SAMPLE_WARNING",bool(base.TRADES<30))
        print("CONCENTRATION_WARNING",bool(base.TOP_SYMBOL_SHARE>60.0))
    else:
        print("BASELINE_FOUND False")
    print(f"ELAPSED_SEC {time.time()-t0:.2f}")
    print("DONE", flush=True)

if __name__=="__main__":
    main()
