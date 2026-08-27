#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.v240d_precomputed_fast_validate import load_1m_bars, run_williams, run_dbb
from tools.v240_validate_soxl_soxs_two_engines import metrics


def audit(df: pd.DataFrame):
    if df.empty:
        return {"trades":0}
    out={"trades":int(len(df))}
    for c in ["net_return","mfe","mae"]:
        if c in df.columns:
            s=pd.to_numeric(df[c],errors="coerce")
            out[c+"_median_pct"]=float(s.median()*100)
            out[c+"_p25_pct"]=float(s.quantile(.25)*100)
            out[c+"_p75_pct"]=float(s.quantile(.75)*100)
    if "exit_reason" in df.columns:
        out["exit_reasons"]=df["exit_reason"].value_counts().to_dict()
    if "symbol" in df.columns:
        out["symbols"]=df["symbol"].value_counts().to_dict()
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default="/home/ubuntu/day-trader-api/daytrader.db")
    ap.add_argument("--max-days",type=int,default=135)
    ap.add_argument("--cost-bps",type=float,default=8.0)
    ap.add_argument("--fallback-risk-pct",type=float,default=0.015)
    ap.add_argument("--max-swing-risk-pct",type=float,default=0.025)
    ap.add_argument("--out-dir",default="/home/ubuntu/day-trader-api/validation_v240e")
    a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0)
    dates=sorted(bars["date_et"].unique())[-a.max_days:]
    bars=bars[bars["date_et"].isin(dates)].copy().reset_index(drop=True)
    print(f"V240E SOURCE={table} DAYS={len(dates)} BARS={len(bars)}",flush=True)
    wt=run_williams(bars,a.cost_bps,a.fallback_risk_pct,a.max_swing_risk_pct)
    dt=run_dbb(bars,a.cost_bps,a.fallback_risk_pct,a.max_swing_risk_pct)
    wm=metrics(wt); dm=metrics(dt)
    print("WILLIAMS_METRICS=",json.dumps(wm,ensure_ascii=False),flush=True)
    print("DOUBLE_BOLLINGER_METRICS=",json.dumps(dm,ensure_ascii=False),flush=True)
    wa=audit(pd.DataFrame(wt)); da=audit(pd.DataFrame(dt))
    print("WILLIAMS_AUDIT=",json.dumps(wa,ensure_ascii=False),flush=True)
    print("DOUBLE_BOLLINGER_AUDIT=",json.dumps(da,ensure_ascii=False),flush=True)
    def eligible(m): return m["trades"]>=20 and m["pf"]>1 and m["net_pct"]>0
    if eligible(dm) and not eligible(wm): winner="DOUBLE_BOLLINGER"
    elif eligible(wm) and not eligible(dm): winner="WILLIAMS"
    elif eligible(dm) and eligible(wm): winner="DOUBLE_BOLLINGER" if (dm["pf"],dm["net_pct"],dm["max_dd_pct"])>(wm["pf"],wm["net_pct"],wm["max_dd_pct"]) else "WILLIAMS"
    else: winner="NONE"
    print("WINNER_135D=",winner,flush=True)
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(wt).to_csv(out/"williams_trades.csv",index=False)
    pd.DataFrame(dt).to_csv(out/"double_bollinger_trades.csv",index=False)
    (out/"summary.json").write_text(json.dumps({"source":table,"days":len(dates),"williams":wm,"double_bollinger":dm,"williams_audit":wa,"double_bollinger_audit":da,"winner":winner},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
