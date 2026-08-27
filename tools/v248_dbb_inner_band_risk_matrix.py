#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars
from tools.v240d_precomputed_fast_validate import prep_symbol, rsi, macd, dbb_setup_row

SYMBOLS=("SOXL","SOXS")


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
        b5['inner_u2']=mid+0.5*sd
        b5['inner_l2']=mid-0.5*sd
        b5['inner_width']=b5['inner_u2']-b5['inner_l2']
        b5['outer_u2']=mid+3.0*sd
        b5['outer_l2']=mid-3.0*sd
        b5['outer_width']=b5['outer_u2']-b5['outer_l2']
        b5['inner_l_prev2']=b5['inner_l2'].shift(1)
        b5['close5_prev2']=b5['close'].shift(1)
        t=pd.to_datetime(x['et']); x['_b']=t.dt.floor('5min')-pd.Timedelta(minutes=5)
        use=b5[['bucket_raw','inner_l2','inner_width','outer_width','inner_l_prev2','close5_prev2']].rename(columns={'bucket_raw':'_b'})
        data[sym]=x.merge(use,on='_b',how='left').drop(columns=['_b'])
    return data


def add_trade(trades,policy,sym,entry,partial_price,final_price,partial_done,reason,cost_bps,mfe,mae,initial_r,final_r,adapt_count):
    fee=cost_bps/10000.0
    gross=(0.5*(partial_price/entry-1.0)+0.5*(final_price/entry-1.0)) if partial_done else (final_price/entry-1.0)
    net=gross-2.0*fee
    trades.append({'policy':policy,'symbol':sym,'entry_price':entry,'partial_price':partial_price if partial_done else None,
                   'exit_price':final_price,'partial_done':bool(partial_done),'exit_reason':reason,'gross_return':gross,
                   'net_return':net,'mfe':mfe,'mae':mae,'initial_r_pct':initial_r/entry,'final_r_pct':final_r/entry,
                   'adapt_count':adapt_count})


def metrics(trades):
    if not trades: return {'trades':0}
    r=pd.Series([t['net_return'] for t in trades],dtype=float); gp=float(r[r>0].sum()); gl=float(-r[r<0].sum())
    eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    df=pd.DataFrame(trades)
    return {'trades':len(trades),'wins':int((r>0).sum()),'win_rate':float((r>0).mean()),
            'net_pct':float((eq.iloc[-1]-1)*100),'pf':float(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0),
            'avg_pct':float(r.mean()*100),'max_dd_pct':float(dd.min()*100),
            'avg_mfe_pct':float(df['mfe'].mean()*100),'avg_mae_pct':float(df['mae'].mean()*100),
            'partial_2r_count':int(df['partial_done'].sum()),'adapted_trades':int((df['adapt_count']>0).sum()),
            'avg_initial_r_pct':float(df['initial_r_pct'].mean()*100),'avg_final_r_pct':float(df['final_r_pct'].mean()*100),
            'exit_reasons':df['exit_reason'].value_counts().to_dict()}


POLICIES={
    'INNER_075': {'mult':0.75},
    'INNER_100': {'mult':1.00},
    'INNER_125': {'mult':1.25},
    'INNER_150': {'mult':1.50},
    'ADAPT_OUTER_125_STOP_ONLY': {'mult':1.00,'outer_trigger':1.25,'cap_mult':1.50,'recalc_target':False},
    'ADAPT_OUTER_125_RECALC_2R': {'mult':1.00,'outer_trigger':1.25,'cap_mult':1.50,'recalc_target':True},
    'ADAPT_OUTER_150_STOP_ONLY': {'mult':1.00,'outer_trigger':1.50,'cap_mult':1.50,'recalc_target':False},
}


def replay(data,cost_bps,policy_name,cfg):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={s:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for s,df in data.items()}
    for t in all_times:
        if pos:
            sym,entry_i,entry,initial_r,current_r,stop,target,partial_done,partial_price,entry_outer,mfe,mae,adapt_count=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); hi=float(x.at[i,'high']); lo=float(x.at[i,'low'])
                mfe=max(mfe,hi/entry-1.0); mae=min(mae,lo/entry-1.0)

                # Optional volatility-expansion adaptation BEFORE the first 2R partial.
                if (not partial_done) and cfg.get('outer_trigger') and pd.notna(x.at[i,'outer_width']) and entry_outer>0:
                    ratio=float(x.at[i,'outer_width'])/entry_outer
                    if ratio>=float(cfg['outer_trigger']) and pd.notna(x.at[i,'inner_width']):
                        candidate=float(x.at[i,'inner_width'])
                        capped=min(candidate, initial_r*float(cfg.get('cap_mult',1.5)))
                        new_r=max(current_r,capped)
                        if new_r>current_r+1e-12:
                            current_r=new_r; stop=entry-current_r; adapt_count+=1
                            if cfg.get('recalc_target'): target=entry+2.0*current_r

                if lo<=stop:
                    add_trade(trades,policy_name,sym,entry,partial_price,stop,partial_done,'INITIAL_OR_ADAPTED_STOP',cost_bps,mfe,mae,initial_r,current_r,adapt_count)
                    pos=None; continue

                if (not partial_done) and hi>=target:
                    partial_done=True; partial_price=target

                # After 2R partial, remaining 50% exits only on completed 5m cross below inner lower band (20MA - 0.5 sigma).
                if partial_done:
                    lower=x.at[i,'inner_l2']; lower_prev=x.at[i,'inner_l_prev2']; close5=x.at[i,'close5']; prev=x.at[i,'close5_prev2']
                    if all(pd.notna(v) for v in (lower,lower_prev,close5,prev)):
                        if float(prev)>=float(lower_prev) and float(close5)<float(lower):
                            add_trade(trades,policy_name,sym,entry,partial_price,p,True,'INNER_LOWER_05SIGMA_CROSS',cost_bps,mfe,mae,initial_r,current_r,adapt_count)
                            pos=None; continue

                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    add_trade(trades,policy_name,sym,entry,partial_price,p,partial_done,'SESSION_CLOSE',cost_bps,mfe,mae,initial_r,current_r,adapt_count)
                    pos=None; continue
                pos=(sym,entry_i,entry,initial_r,current_r,stop,target,partial_done,partial_price,entry_outer,mfe,mae,adapt_count)

        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=dbb_setup_row(x.loc[i])
                if not ok or not one_min_timing(x,i): continue
                if pd.isna(x.at[i,'inner_width']) or float(x.at[i,'inner_width'])<=0 or pd.isna(x.at[i,'outer_width']): continue
                entry=float(x.at[i,'close']); initial_r=float(x.at[i,'inner_width'])*float(cfg['mult'])
                if initial_r<=0: continue
                stop=entry-initial_r
                if stop<=0: continue
                target=entry+2.0*initial_r
                cands.append((score,sym,i,entry,initial_r,stop,target,float(x.at[i,'outer_width'])))
            if cands:
                cands.sort(reverse=True)
                _,sym,i,entry,initial_r,stop,target,entry_outer=cands[0]
                pos=(sym,i,entry,initial_r,initial_r,stop,target,False,None,entry_outer,0.0,0.0,0)
    return trades


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--cost-bps',type=float,default=8.0); a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0); dates=sorted(bars['date_et'].unique())[-a.max_days:]; bars=bars[bars['date_et'].isin(dates)].copy()
    data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}; data=enrich_bands(data)
    print(f'V248 SOURCE={table} DAYS={len(dates)} BARS={len(bars)} ENTRY=V240D_ORIGINAL EXIT=2R_HALF_THEN_INNER_LOWER_05SIGMA',flush=True)
    results={}
    for name,cfg in POLICIES.items():
        tr=replay(data,a.cost_bps,name,cfg); m=metrics(tr); results[name]=m
        print(name+'_METRICS=',json.dumps(m,ensure_ascii=False),flush=True)
    ranked=sorted(results.items(),key=lambda kv:(kv[1].get('pf',0),kv[1].get('net_pct',-999),kv[1].get('max_dd_pct',-999)),reverse=True)
    print('RANKING=',json.dumps([{'policy':k,**v} for k,v in ranked],ensure_ascii=False),flush=True)

if __name__=='__main__': main()
