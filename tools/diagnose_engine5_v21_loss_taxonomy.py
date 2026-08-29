from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
import tools.diagnose_engine5_v19_strength_score as v19
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OPEN_MINUTE=9*60+10
THRESHOLD=50


def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}

def q(v):
    try:
        x=float(v); return x if np.isfinite(x) else np.nan
    except Exception:return np.nan

def metrics(df):
    p=pd.to_numeric(df.pnl_pct,errors='coerce').dropna(); gp=p[p>0].sum(); gl=-p[p<0].sum()
    return len(p),int((p>0).sum()),int((p<=0).sum()),float((p>0).mean()*100),float(p.sum()),float(gp/gl if gl>0 else np.inf)

def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames0=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in frames0.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17b,_,_=v17b.build_v17b(ev16,scored,waits)
    trades=v17c.simulate_unconditional_hwm(packed,ev17b,states,THRESHOLD)
    frames=v19.build_score_frames(scored)
    rows=[]
    for tr in trades.itertuples(index=False):
        sym=str(tr.symbol).zfill(6); ts=pd.Timestamp(tr.entry_time); r=v19.row_at_or_before(frames,sym,ts); d=v19.score_row(r)
        close=q(r.get('close',np.nan)) if r is not None else np.nan; ou=q(r.get('outer_upper',np.nan)) if r is not None else np.nan
        prox=close/ou if np.isfinite(close) and np.isfinite(ou) and ou!=0 else np.nan
        minute=ts.hour*60+ts.minute
        edge=q(d.get('ratio_edge')); rsi=q(d.get('rsi')); prev_rsi=q(d.get('prev_rsi')); ms=q(d.get('macd_slope')); prev_ms=q(d.get('prev_macd_slope'))
        mature=bool(np.isfinite(rsi) and rsi>=70 and d.get('ratio_mode')=='RATIO' and np.isfinite(edge) and edge<=.10)
        negedge=bool(d.get('ratio_mode')=='RATIO' and np.isfinite(edge) and edge<=0)
        decel=bool(np.isfinite(ms) and np.isfinite(prev_ms) and ms>0 and prev_ms>0 and ms<prev_ms)
        outer=bool(np.isfinite(prox) and prox>=.985)
        late=minute>=14*60
        opening=minute<10*60
        rows.append(dict(symbol=sym,entry_time=ts,pnl_pct=float(tr.pnl_pct),reason=str(tr.reason),breakout=bool(getattr(tr,'breakout_entry',False)),rsi=rsi,prev_rsi=prev_rsi,ratio_edge=edge,ratio_mode=d.get('ratio_mode'),macd_slope=ms,prev_macd_slope=prev_ms,outer_proximity=prox,mature_weak_edge=mature,negative_edge=negedge,macd_decel=decel,outer_zone=outer,opening=opening,late=late))
    z=pd.DataFrame(rows); losses=z[z.pnl_pct<=0].copy(); wins=z[z.pnl_pct>0].copy()
    print('=== V17C LOSS TAXONOMY — DIAGNOSTIC ONLY ===')
    print('No entry/exit change. Goal: explain the 48 baseline losses before another rule is invented.')
    n,w,l,wr,g,pf=metrics(z); print(f'BASELINE trades={n} wins={w} losses={l} win={wr:.2f}% gross={g:+.4f}% pf={pf:.3f}')
    flags=['mature_weak_edge','negative_edge','macd_decel','outer_zone','opening','late']
    print('\n=== FEATURE PREVALENCE: LOSS VS WIN ===')
    for c in flags:
        lr=losses[c].mean()*100 if len(losses) else 0; wr2=wins[c].mean()*100 if len(wins) else 0
        print(f'{c:18s} loss={int(losses[c].sum()):2d}/{len(losses)} {lr:6.2f}% | win={int(wins[c].sum()):2d}/{len(wins)} {wr2:6.2f}% | lift={lr-wr2:+6.2f}pp')
    print('\n=== LOSS REASON ==='); print(losses.groupby('reason').agg(n=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).sort_values('gross').to_string())
    print('\n=== LOSS TIME BUCKET ==='); losses['hour']=losses.entry_time.dt.hour; print(losses.groupby('hour').agg(n=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).to_string())
    print('\n=== WORST 25 WITH FEATURES ===')
    cols=['symbol','entry_time','pnl_pct','reason','breakout','rsi','ratio_edge','macd_slope','prev_macd_slope','outer_proximity']+flags
    print(losses.sort_values('pnl_pct').head(25)[cols].to_string(index=False))
    print('\n=== COMBINATION COUNTS AMONG LOSSES ===')
    losses['feature_count']=losses[flags].sum(axis=1); wins['feature_count']=wins[flags].sum(axis=1)
    for k in range(0,len(flags)+1): print(f'count={k}: losses={(losses.feature_count==k).sum()} wins={(wins.feature_count==k).sum()}')
    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v21_v17c_loss_taxonomy.csv'; z.to_csv(out,index=False); print('\n[CSV]',out)

if __name__=='__main__':main()
