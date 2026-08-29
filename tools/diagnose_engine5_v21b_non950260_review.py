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
EXCLUDE='950260'


def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}

def fv(r,name):
    try:
        x=float(r.get(name,np.nan)); return x if np.isfinite(x) else np.nan
    except Exception:return np.nan

def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    raw_frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17b,_,_=v17b.build_v17b(ev16,scored,waits)
    trades=v17c.simulate_unconditional_hwm(packed,ev17b,states,THRESHOLD)
    frames=v19.build_score_frames(scored)
    rows=[]
    for tr in trades.itertuples(index=False):
        sym=str(tr.symbol).zfill(6)
        if sym==EXCLUDE: continue
        ts=pd.Timestamp(tr.entry_time); r=v19.row_at_or_before(frames,sym,ts); d=v19.score_row(r)
        close=fv(r,'close') if r is not None else np.nan; ou=fv(r,'outer_upper') if r is not None else np.nan
        prox=close/ou if np.isfinite(close) and np.isfinite(ou) and ou else np.nan
        edge=float(d['ratio_edge']) if np.isfinite(d.get('ratio_edge',np.nan)) else np.nan
        rsi=float(d['rsi']) if np.isfinite(d.get('rsi',np.nan)) else np.nan
        ms=float(d['macd_slope']) if np.isfinite(d.get('macd_slope',np.nan)) else np.nan
        pms=float(d['prev_macd_slope']) if np.isfinite(d.get('prev_macd_slope',np.nan)) else np.nan
        minute=ts.hour*60+ts.minute
        rows.append(dict(symbol=sym,entry_time=ts,pnl_pct=float(tr.pnl_pct),reason=str(tr.reason),breakout=bool(getattr(tr,'breakout_entry',False)),rsi=rsi,ratio_edge=edge,ratio_mode=d.get('ratio_mode'),macd_slope=ms,prev_macd_slope=pms,outer_proximity=prox,mature_weak_edge=bool(np.isfinite(rsi) and rsi>=70 and d.get('ratio_mode')=='RATIO' and np.isfinite(edge) and edge<=.10),negative_edge=bool(d.get('ratio_mode')=='RATIO' and np.isfinite(edge) and edge<=0),macd_decel=bool(np.isfinite(ms) and np.isfinite(pms) and ms>0 and pms>0 and ms<pms),outer_zone=bool(np.isfinite(prox) and prox>=.985),opening=bool(minute<10*60),late=bool(minute>=14*60)))
    z=pd.DataFrame(rows); losses=z[z.pnl_pct<=0].copy(); wins=z[z.pnl_pct>0].copy()
    print('=== V17C REVIEW EXCLUDING 950260 ===')
    print('950260 is excluded from this diagnostic only. V17C logic itself is unchanged.')
    print(f'ALL_NON950260 trades={len(z)} wins={(z.pnl_pct>0).sum()} losses={(z.pnl_pct<=0).sum()} win={(z.pnl_pct>0).mean()*100:.2f}% gross={z.pnl_pct.sum():+.4f}%')
    print('\n=== BY SYMBOL ===')
    by=z.groupby('symbol').agg(trades=('pnl_pct','size'),wins=('pnl_pct',lambda s:int((s>0).sum())),losses=('pnl_pct',lambda s:int((s<=0).sum())),gross=('pnl_pct','sum'),avg=('pnl_pct','mean'))
    by['win_rate']=by['wins']/by['trades']*100
    print(by.sort_values(['win_rate','gross']).to_string())
    print('\n=== LOSS FEATURE PREVALENCE ===')
    flags=['mature_weak_edge','negative_edge','macd_decel','outer_zone','opening','late']
    for c in flags:
        lp=losses[c].mean()*100 if len(losses) else 0; wp=wins[c].mean()*100 if len(wins) else 0
        print(f'{c:18s} loss={int(losses[c].sum()):2d}/{len(losses)} {lp:6.2f}% | win={int(wins[c].sum()):2d}/{len(wins)} {wp:6.2f}% | lift={lp-wp:+6.2f}pp')
    print('\n=== WORST 25 NON-950260 ===')
    cols=['symbol','entry_time','pnl_pct','reason','breakout','rsi','ratio_edge','macd_slope','prev_macd_slope','outer_proximity']+flags
    print(losses.sort_values('pnl_pct').head(25)[cols].to_string(index=False))
    print('\n=== LOSS REASON BY SYMBOL ===')
    print(losses.groupby(['symbol','reason']).agg(n=('pnl_pct','size'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).sort_values('gross').to_string())
    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v21b_non950260_review.csv'; z.to_csv(out,index=False); print('\n[CSV]',out)

if __name__=='__main__':main()
