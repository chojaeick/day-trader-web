#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from tools.v240d_precomputed_fast_validate import load_1m_bars, prep_symbol, replay_dbb, SYMBOLS
from tools.v240_validate_soxl_soxs_two_engines import metrics


def summarize(trades):
    df=pd.DataFrame(trades)
    if df.empty:
        return {"trades":0}
    out={"trades":len(df)}
    for col in ("net_return","mfe","mae"):
        s=pd.to_numeric(df[col],errors="coerce")
        out[col]={
            "mean_pct":float(s.mean()*100),
            "median_pct":float(s.median()*100),
            "p25_pct":float(s.quantile(.25)*100),
            "p75_pct":float(s.quantile(.75)*100),
        }
    out["mfe_ge_1pct"]=int((df["mfe"]>=.01).sum())
    out["mfe_ge_2pct"]=int((df["mfe"]>=.02).sum())
    out["mfe_ge_3pct"]=int((df["mfe"]>=.03).sum())
    out["mae_le_neg1pct"]=int((df["mae"]<=-.01).sum())
    out["mfe_ge_1_but_loss"]=int(((df["mfe"]>=.01)&(df["net_return"]<0)).sum())
    out["mfe_ge_2_but_loss"]=int(((df["mfe"]>=.02)&(df["net_return"]<0)).sum())
    out["exit_reasons"]=df["exit_reason"].value_counts().to_dict()
    out["symbols"]=df["symbol"].value_counts().to_dict()
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db')
    ap.add_argument('--max-days',type=int,default=135)
    ap.add_argument('--cost-bps',type=float,default=8.0)
    ap.add_argument('--fallback-risk-pct',type=float,default=.015)
    ap.add_argument('--max-swing-risk-pct',type=float,default=.025)
    ap.add_argument('--out-dir',default='/home/ubuntu/day-trader-api/validation_v242')
    a=ap.parse_args()

    bars,table=load_1m_bars(a.db,0)
    dates=sorted(bars['date_et'].unique())[-a.max_days:]
    bars=bars[bars['date_et'].isin(dates)].copy()
    data={sym:prep_symbol(bars[bars['symbol']==sym].copy()) for sym in SYMBOLS}
    trades=replay_dbb(data,a.fallback_risk_pct,a.max_swing_risk_pct,a.cost_bps)
    df=pd.DataFrame(trades)
    print(f'V242 SOURCE={table} DAYS={len(dates)} TRADES={len(df)}',flush=True)
    print('METRICS=',json.dumps(metrics(trades),ensure_ascii=False),flush=True)
    print('AUDIT=',json.dumps(summarize(trades),ensure_ascii=False),flush=True)

    if not df.empty:
        loser_after_1=df[(df['mfe']>=.01)&(df['net_return']<0)].sort_values('mfe',ascending=False).head(20)
        clean_losses=df.sort_values('mae').head(20)
        winners=df.sort_values('net_return',ascending=False).head(20)
    else:
        loser_after_1=clean_losses=winners=df
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    df.to_csv(out/'all_trades.csv',index=False)
    loser_after_1.to_csv(out/'gave_back_after_1pct_mfe.csv',index=False)
    clean_losses.to_csv(out/'worst_mae.csv',index=False)
    winners.to_csv(out/'best_winners.csv',index=False)
    print('OUTPUT_DIR=',out,flush=True)
    print('INTERPRETATION=high MFE then loss => exit problem; low MFE and deep MAE => entry problem',flush=True)

if __name__=='__main__':
    main()
