from __future__ import annotations

"""Audit how much of US mapped Engine5 performance is caused by the fixed fee assumption.

Reads already-generated mapped trade CSV only. No DB access. No feature rebuild. No re-simulation.
Reports gross (0 fee) and fee sensitivity at several round-trip fee assumptions.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
TRADES=ROOT/'us_kr_mapped_all_versions_trades.csv'
OUT=ROOT/'us_kr_mapped_fee_sensitivity.csv'
FEES=(0.00,0.05,0.10,0.15,0.20,0.25)


def stats(gross: pd.Series, fee: float):
    net=gross-float(fee)
    gp=float(net[net>0].sum())
    gl=float(-net[net<0].sum())
    return dict(
        fee_rt_pct=float(fee),
        trades=int(len(net)),
        wins=int((net>0).sum()),
        losses=int((net<=0).sum()),
        win_pct=float((net>0).mean()*100.0) if len(net) else 0.0,
        gross_sum_pct=float(gross.sum()),
        gross_avg_pct=float(gross.mean()) if len(gross) else np.nan,
        net_sum_pct=float(net.sum()),
        net_avg_pct=float(net.mean()) if len(net) else np.nan,
        pf=float(gp/gl) if gl>0 else np.inf,
        max_loss_pct=float(net.min()) if len(net) else np.nan,
    )


def main():
    if not TRADES.exists():
        raise FileNotFoundError(TRADES)
    df=pd.read_csv(TRADES)
    if 'variant' not in df or 'pnl_pct' not in df:
        raise ValueError('expected variant,pnl_pct columns')
    df['pnl_pct']=pd.to_numeric(df['pnl_pct'],errors='coerce')
    df=df.dropna(subset=['variant','pnl_pct']).copy()

    rows=[]
    print('=== US KR-MAPPED FEE SENSITIVITY AUDIT ===',flush=True)
    print('SOURCE: existing trades CSV only. NO RESIMULATION.',flush=True)
    print('pnl_pct is treated as gross strategy return; fee is subtracted once per completed trade.',flush=True)

    for variant,g in df.groupby('variant',sort=False):
        gross=g['pnl_pct'].astype(float)
        gross_row=stats(gross,0.0)
        fee25=stats(gross,0.25)
        print(f"{variant}: trades={len(gross)} gross_avg={gross_row['gross_avg_pct']:+.6f}% gross_sum={gross_row['gross_sum_pct']:+.4f}% gross_WR={gross_row['win_pct']:.2f}% gross_PF={gross_row['pf']:.3f} | fee0.25 net={fee25['net_sum_pct']:+.4f}% WR={fee25['win_pct']:.2f}% PF={fee25['pf']:.3f}",flush=True)
        for fee in FEES:
            rows.append(dict(variant=variant,**stats(gross,fee)))

    out=pd.DataFrame(rows)
    out.to_csv(OUT,index=False)
    print('WROTE',OUT,flush=True)

    print('\n=== BREAK-EVEN ROUND-TRIP COST (gross average) ===',flush=True)
    for variant,g in df.groupby('variant',sort=False):
        x=float(g.pnl_pct.mean())
        print(f'{variant}: {x:.6f}% per trade',flush=True)

if __name__=='__main__':
    main()
