#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars
from tools.v240d_precomputed_fast_validate import prep_symbol, rsi, macd, dbb_setup_row

SYMBOLS=('SOXL','SOXS')


def one_min_timing(x,i):
    sl=x['close'].iloc[max(0,i-40):i+1].reset_index(drop=True)
    if len(sl)<29: return False
    rr=rsi(sl,14); m,s,h=macd(sl)
    if pd.isna(rr.iat[-1]) or pd.isna(rr.iat[-2]): return False
    return bool(((rr.iat[-1]>=rr.iat[-2]) or (h.iat[-1]>=h.iat[-2])) and
                (m.iat[-1]>s.iat[-1] or (m.iat[-2]<=s.iat[-2] and m.iat[-1]>s.iat[-1])) and
                not (rr.iat[-2]>=70>rr.iat[-1]))


def enrich_bands(data):
    for sym,x in data.items():
        q=x.set_index(pd.to_datetime(x['et']))
        b5=q.resample('5min',label='left',closed='left').agg({'close':'last'}).dropna().reset_index(names='bucket_raw')
        mid=b5['close'].rolling(20).mean(); sd=b5['close'].rolling(20).std(ddof=0)
        b5['mid2']=mid; b5['mid_prev2']=mid.shift(1)
        b5['iu2']=mid+0.5*sd; b5['il2']=mid-0.5*sd
        b5['ou2']=mid+3.0*sd; b5['ol2']=mid-3.0*sd
        b5['iw2']=b5['iu2']-b5['il2']; b5['ow2']=b5['ou2']-b5['ol2']
        b5['ow_prev2']=b5['ow2'].shift(1)
        b5['il_prev2']=b5['il2'].shift(1)
        b5['close_prev2']=b5['close'].shift(1)
        t=pd.to_datetime(x['et']); x['_b2']=t.dt.floor('5min')-pd.Timedelta(minutes=5)
        use=b5[['bucket_raw','mid2','mid_prev2','iu2','il2','ou2','ol2','iw2','ow2','ow_prev2','il_prev2','close_prev2']].rename(columns={'bucket_raw':'_b2'})
        data[sym]=x.merge(use,on='_b2',how='left').drop(columns=['_b2'])
    return data


def add_trade(trades,sym,entry,partial_price,final_price,partial_done,reason,cost_bps,mfe,mae,initial_r,final_r,adapted):
    fee=cost_bps/10000.0
    gross=(0.5*(partial_price/entry-1.0)+0.5*(final_price/entry-1.0)) if partial_done else (final_price/entry-1.0)
    net=gross-2.0*fee
    trades.append({'symbol':sym,'entry_price':entry,'partial_price':partial_price if partial_done else None,
                   'exit_price':final_price,'partial_done':bool(partial_done),'exit_reason':reason,
                   'gross_return':gross,'net_return':net,'mfe':mfe,'mae':mae,
                   'initial_r_pct':initial_r/entry,'final_r_pct':final_r/entry,'adapted':bool(adapted)})


def metrics(trades):
    if not trades: return {'trades':0}
    df=pd.DataFrame(trades); r=df['net_return'].astype(float)
    gp=float(r[r>0].sum()); gl=float(-r[r<0].sum()); eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    return {'trades':len(df),'wins':int((r>0).sum()),'win_rate':float((r>0).mean()),
            'net_pct':float((eq.iloc[-1]-1)*100),'pf':float(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0),
            'avg_pct':float(r.mean()*100),'max_dd_pct':float(dd.min()*100),
            'avg_mfe_pct':float(df['mfe'].mean()*100),'avg_mae_pct':float(df['mae'].mean()*100),
            'partial_outer_count':int(df['partial_done'].sum()),'adapted_trades':int(df['adapted'].sum()),
            'avg_initial_r_pct':float(df['initial_r_pct'].mean()*100),'avg_final_r_pct':float(df['final_r_pct'].mean()*100),
            'exit_reasons':df['exit_reason'].value_counts().to_dict()}


def replay(data,cost_bps,adapt_cap):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={s:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for s,df in data.items()}
    for t in all_times:
        if pos:
            sym,ei,entry,entry_time,initial_r,current_r,stop,partial_done,partial_price,mfe,mae,adapted=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); hi=float(x.at[i,'high']); lo=float(x.at[i,'low'])
                mfe=max(mfe,hi/entry-1.0); mae=min(mae,lo/entry-1.0)

                # Only during first 5 minutes after entry: if DBB midline is rising AND outer band width expands,
                # widen stop by the observed inner-width expansion, capped by initial R * adapt_cap.
                elapsed=(pd.Timestamp(x.at[i,'et'])-entry_time).total_seconds()
                if elapsed<=300 and all(pd.notna(x.at[i,c]) for c in ('mid2','mid_prev2','ow2','ow_prev2','iw2')):
                    mid_rising=float(x.at[i,'mid2'])>float(x.at[i,'mid_prev2'])
                    outer_expanding=float(x.at[i,'ow2'])>float(x.at[i,'ow_prev2'])
                    if mid_rising and outer_expanding:
                        desired=max(current_r,float(x.at[i,'iw2']))
                        desired=min(desired,initial_r*adapt_cap)
                        if desired>current_r+1e-12:
                            current_r=desired; stop=entry-current_r; adapted=True

                if lo<=stop:
                    add_trade(trades,sym,entry,partial_price,stop,partial_done,'ADAPTIVE_STOP',cost_bps,mfe,mae,initial_r,current_r,adapted)
                    pos=None; continue

                # First profit action: touch current completed-5m outer upper band, sell 50%.
                if (not partial_done) and pd.notna(x.at[i,'ou2']) and hi>=float(x.at[i,'ou2']):
                    partial_done=True; partial_price=float(x.at[i,'ou2'])

                # Runner exits when price touches/breaks the lower inner band (20MA - 0.5 sigma).
                if partial_done and pd.notna(x.at[i,'il2']) and lo<=float(x.at[i,'il2']):
                    add_trade(trades,sym,entry,partial_price,float(x.at[i,'il2']),True,'INNER_LOWER_TOUCH',cost_bps,mfe,mae,initial_r,current_r,adapted)
                    pos=None; continue

                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    add_trade(trades,sym,entry,partial_price,p,partial_done,'SESSION_CLOSE',cost_bps,mfe,mae,initial_r,current_r,adapted)
                    pos=None; continue
                pos=(sym,ei,entry,entry_time,initial_r,current_r,stop,partial_done,partial_price,mfe,mae,adapted)

        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=dbb_setup_row(x.loc[i])
                if not ok or not one_min_timing(x,i): continue
                if pd.isna(x.at[i,'iw2']) or float(x.at[i,'iw2'])<=0: continue
                entry=float(x.at[i,'close']); initial_r=float(x.at[i,'iw2']); stop=entry-initial_r
                if stop<=0: continue
                cands.append((score,sym,i,entry,initial_r,stop,pd.Timestamp(x.at[i,'et'])))
            if cands:
                cands.sort(reverse=True); _,sym,i,entry,initial_r,stop,entry_time=cands[0]
                pos=(sym,i,entry,entry_time,initial_r,initial_r,stop,False,None,0.0,0.0,False)
    return trades


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--cost-bps',type=float,default=8.0); a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0); dates=sorted(bars['date_et'].unique())[-a.max_days:]; bars=bars[bars['date_et'].isin(dates)].copy()
    data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}; data=enrich_bands(data)
    print(f'V250 SOURCE={table} DAYS={len(dates)} BARS={len(bars)} ENTRY=V240D_ORIGINAL SLOPE_FILTER=OFF',flush=True)
    for cap in (1.0,1.25,1.5,1.75):
        tr=replay(data,a.cost_bps,cap); print(f'CAP_{cap:.2f}_METRICS=',json.dumps(metrics(tr),ensure_ascii=False),flush=True)
if __name__=='__main__': main()
