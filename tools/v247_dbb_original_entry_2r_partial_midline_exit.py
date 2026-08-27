#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars
from tools.v240d_precomputed_fast_validate import (
    prep_symbol, confirmed_swing_low, choose_stop, rsi, macd, dbb_setup_row
)

SYMBOLS=("SOXL","SOXS")

def one_min_timing(x,i):
    sl=x['close'].iloc[max(0,i-40):i+1].reset_index(drop=True)
    if len(sl)<29: return False
    rr=rsi(sl,14); m,s,h=macd(sl)
    if pd.isna(rr.iat[-1]) or pd.isna(rr.iat[-2]): return False
    return bool(((rr.iat[-1]>=rr.iat[-2]) or (h.iat[-1]>=h.iat[-2])) and
                (m.iat[-1]>s.iat[-1] or (m.iat[-2]<=s.iat[-2] and m.iat[-1]>s.iat[-1])) and
                not (rr.iat[-2]>=70>rr.iat[-1]))

def ensure_inner_lower(data):
    for sym,x in data.items():
        q=x.set_index(pd.to_datetime(x['et']))
        b5=q.resample('5min',label='left',closed='left').agg({'close':'last'}).dropna().reset_index(names='bucket_raw')
        mid=b5['close'].rolling(20).mean(); sd=b5['close'].rolling(20).std(ddof=0)
        # Double Bollinger inner band = 20MA +/- 1 sigma. Runner exits on lower inner band.
        b5['inner_l']=mid-sd
        b5['inner_l_prev']=b5['inner_l'].shift(1)
        b5['close5_prev_for_inner_l']=b5['close'].shift(1)
        t=pd.to_datetime(x['et']); x['_inner_bucket']=t.dt.floor('5min')-pd.Timedelta(minutes=5)
        m=b5[['bucket_raw','inner_l','inner_l_prev','close5_prev_for_inner_l']].rename(columns={'bucket_raw':'_inner_bucket'})
        data[sym]=x.merge(m,on='_inner_bucket',how='left').drop(columns=['_inner_bucket'])
    return data

def add_trade(trades,sym,entry,partial_price,final_price,partial_done,reason,cost_bps,mfe,mae):
    fee=cost_bps/10000.0
    gross=(0.5*(partial_price/entry-1.0)+0.5*(final_price/entry-1.0)) if partial_done else (final_price/entry-1.0)
    net=gross-2.0*fee
    trades.append({'engine':'DBB_ORIGINAL_ENTRY_2R_HALF_INNER_LOWER','symbol':sym,'entry_price':entry,
                   'partial_price':partial_price if partial_done else None,'exit_price':final_price,
                   'partial_done':bool(partial_done),'exit_reason':reason,'gross_return':gross,'net_return':net,
                   'mfe':mfe,'mae':mae})

def metrics(trades):
    if not trades: return {'trades':0,'wins':0,'win_rate':0.0,'net_pct':0.0,'pf':0.0,'avg_pct':0.0,'avg_mfe_pct':0.0,'avg_mae_pct':0.0,'max_dd_pct':0.0}
    r=pd.Series([t['net_return'] for t in trades],dtype=float); wins=int((r>0).sum()); gp=float(r[r>0].sum()); gl=float(-r[r<0].sum())
    eq=(1.0+r).cumprod(); dd=eq/eq.cummax()-1.0
    return {'trades':len(trades),'wins':wins,'win_rate':wins/len(trades),'net_pct':float((eq.iloc[-1]-1)*100),
            'pf':float(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0),'avg_pct':float(r.mean()*100),
            'avg_mfe_pct':float(pd.Series([t['mfe'] for t in trades]).mean()*100),
            'avg_mae_pct':float(pd.Series([t['mae'] for t in trades]).mean()*100),'max_dd_pct':float(dd.min()*100)}

def replay(data,fallback,max_swing,cost_bps):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if pos:
            sym,ei,entry,stop,risk,target,partial_done,partial_price,mfe,mae=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); hi=float(x.at[i,'high']); lo=float(x.at[i,'low'])
                mfe=max(mfe,hi/entry-1.0); mae=min(mae,lo/entry-1.0)
                if lo<=stop:
                    add_trade(trades,sym,entry,partial_price,stop,partial_done,'INITIAL_STOP',cost_bps,mfe,mae); pos=None; continue
                if (not partial_done) and hi>=target:
                    partial_done=True; partial_price=target
                if partial_done:
                    lower=x.at[i,'inner_l']; lower_prev=x.at[i,'inner_l_prev']; close5=x.at[i,'close5']; prev=x.at[i,'close5_prev_for_inner_l']
                    if all(pd.notna(v) for v in (lower,lower_prev,close5,prev)):
                        cross_down=float(prev)>=float(lower_prev) and float(close5)<float(lower)
                        if cross_down:
                            add_trade(trades,sym,entry,partial_price,p,True,'INNER_LOWER_CROSS_DOWN',cost_bps,mfe,mae); pos=None; continue
                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    add_trade(trades,sym,entry,partial_price,p,partial_done,'SESSION_CLOSE',cost_bps,mfe,mae); pos=None; continue
                pos=(sym,ei,entry,stop,risk,target,partial_done,partial_price,mfe,mae)
        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=dbb_setup_row(x.loc[i])
                if not ok or not one_min_timing(x,i): continue
                p=float(x.at[i,'close']); sw=confirmed_swing_low(x['low'],i-1); stop=choose_stop(p,sw,fallback,max_swing)
                if not (0<stop<p): continue
                cands.append((score,sym,i,p,stop))
            if cands:
                cands.sort(reverse=True); _,sym,i,p,stop=cands[0]; risk=p-stop; target=p+2.0*risk
                pos=(sym,i,p,stop,risk,target,False,None,0.0,0.0)
    return trades

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--cost-bps',type=float,default=8.0); ap.add_argument('--fallback-risk-pct',type=float,default=0.015); ap.add_argument('--max-swing-risk-pct',type=float,default=0.025); a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0); dates=sorted(bars['date_et'].unique())[-a.max_days:]; bars=bars[bars['date_et'].isin(dates)].copy()
    data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}; data=ensure_inner_lower(data)
    print(f'V247B SOURCE={table} DAYS={len(dates)} BARS={len(bars)} ENTRY=V240D_ORIGINAL EXIT=2R_HALF_THEN_INNER_LOWER',flush=True)
    tr=replay(data,a.fallback_risk_pct,a.max_swing_risk_pct,a.cost_bps); print('V247B_METRICS=',json.dumps(metrics(tr)),flush=True)
    if tr:
        df=pd.DataFrame(tr); print('PARTIAL_2R_COUNT=',int(df['partial_done'].sum()),flush=True); print('EXIT_REASONS=',json.dumps(df['exit_reason'].value_counts().to_dict()),flush=True); print('SYMBOLS=',json.dumps(df['symbol'].value_counts().to_dict()),flush=True); print('PARTIAL_THEN_LOSS=',int(((df['partial_done']==True)&(df['net_return']<0)).sum()),flush=True)
if __name__=='__main__': main()
