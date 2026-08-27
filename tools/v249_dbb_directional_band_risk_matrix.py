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
        b5['iu']=mid+0.5*sd; b5['il']=mid-0.5*sd
        b5['ou']=mid+3.0*sd; b5['ol']=mid-3.0*sd
        b5['iw']=b5['iu']-b5['il']; b5['ow']=b5['ou']-b5['ol']
        for c in ('iu','il','ou','ol','iw','ow'):
            b5[c+'_prev']=b5[c].shift(1)
        b5['close_prev_dir']=b5['close'].shift(1)
        t=pd.to_datetime(x['et']); x['_b']=t.dt.floor('5min')-pd.Timedelta(minutes=5)
        cols=['bucket_raw','iu','il','ou','ol','iw','ow','iu_prev','il_prev','ou_prev','ol_prev','iw_prev','ow_prev','close_prev_dir']
        m=b5[cols].rename(columns={'bucket_raw':'_b'})
        data[sym]=x.merge(m,on='_b',how='left').drop(columns=['_b'])
    return data


def band_state(r):
    need=['iu','il','ou','ol','iw','ow','iu_prev','il_prev','ou_prev','ol_prev','iw_prev','ow_prev']
    if any(pd.isna(r.get(k)) for k in need): return 'NA'
    iu=float(r['iu']); il=float(r['il']); ou=float(r['ou']); ol=float(r['ol'])
    iup=float(r['iu_prev']); ilp=float(r['il_prev']); oup=float(r['ou_prev']); olp=float(r['ol_prev'])
    iw=float(r['iw']); iwp=float(r['iw_prev']); ow=float(r['ow']); owp=float(r['ow_prev'])
    width_up=(iw>iwp and ow>owp)
    width_down=(iw<iwp and ow<owp)
    bull=(iu>iup and il>=ilp and ou>oup and ol>=olp)
    bear=(iu<=iup and il<ilp and ou<=oup and ol<olp)
    divergent=(iu>iup and il<ilp) or (ou>oup and ol<olp)
    if width_up and bull: return 'BULL_EXPAND'
    if width_up and bear: return 'BEAR_EXPAND'
    if width_up and divergent: return 'DIVERGENT_EXPAND'
    if width_down: return 'CONTRACT'
    if bull: return 'BULL_SHIFT'
    if bear: return 'BEAR_SHIFT'
    return 'MIXED'


def add_trade(trades,policy,sym,entry,partial_price,final_price,partial_done,reason,cost_bps,mfe,mae,initial_r,current_r,bull_adj,bear_adj,states):
    fee=cost_bps/10000.0
    gross=(0.5*(partial_price/entry-1)+0.5*(final_price/entry-1)) if partial_done else final_price/entry-1
    net=gross-2*fee
    trades.append({'policy':policy,'symbol':sym,'entry_price':entry,'partial_price':partial_price if partial_done else None,
                   'exit_price':final_price,'partial_done':partial_done,'exit_reason':reason,'gross_return':gross,'net_return':net,
                   'mfe':mfe,'mae':mae,'initial_r_pct':initial_r/entry,'final_r_pct':current_r/entry,
                   'bull_adjust_count':bull_adj,'bear_adjust_count':bear_adj,'band_states':states})


def metrics(trades):
    if not trades: return {'trades':0}
    df=pd.DataFrame(trades); r=df['net_return'].astype(float); gp=float(r[r>0].sum()); gl=float(-r[r<0].sum())
    eq=(1+r).cumprod(); dd=eq/eq.cummax()-1
    return {'trades':len(df),'wins':int((r>0).sum()),'win_rate':float((r>0).mean()),'net_pct':float((eq.iloc[-1]-1)*100),
            'pf':float(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0),'avg_pct':float(r.mean()*100),
            'max_dd_pct':float(dd.min()*100),'avg_mfe_pct':float(df['mfe'].mean()*100),'avg_mae_pct':float(df['mae'].mean()*100),
            'partial_2r_count':int(df['partial_done'].sum()),'bull_adjusted_trades':int((df['bull_adjust_count']>0).sum()),
            'bear_adjusted_trades':int((df['bear_adjust_count']>0).sum()),'avg_initial_r_pct':float(df['initial_r_pct'].mean()*100),
            'avg_final_r_pct':float(df['final_r_pct'].mean()*100),'exit_reasons':df['exit_reason'].value_counts().to_dict()}


POLICIES={
 'BASE_INNER_100': {'bull_mult':1.0,'bear_mult':1.0},
 'BULL_LOOSEN_125': {'bull_mult':1.25,'bear_mult':1.0},
 'BULL_LOOSEN_150': {'bull_mult':1.50,'bear_mult':1.0},
 'BEAR_TIGHTEN_075': {'bull_mult':1.0,'bear_mult':0.75},
 'DIR_125_075': {'bull_mult':1.25,'bear_mult':0.75},
 'DIR_150_075': {'bull_mult':1.50,'bear_mult':0.75},
}


def replay(data,cost_bps,policy,cfg):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={s:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for s,df in data.items()}
    for t in all_times:
        if pos:
            sym,ei,entry,initial_r,current_r,stop,target,partial_done,partial_price,mfe,mae,bull_adj,bear_adj,states=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); hi=float(x.at[i,'high']); lo=float(x.at[i,'low'])
                mfe=max(mfe,hi/entry-1); mae=min(mae,lo/entry-1)
                st=band_state(x.loc[i]); states[st]=states.get(st,0)+1

                # Before first 2R partial, adapt risk by directional band expansion only.
                if not partial_done:
                    if st=='BULL_EXPAND':
                        desired=max(current_r, initial_r*float(cfg['bull_mult']))
                        if desired>current_r+1e-12:
                            current_r=desired; stop=entry-current_r; bull_adj+=1
                    elif st=='BEAR_EXPAND':
                        desired=min(current_r, initial_r*float(cfg['bear_mult']))
                        if desired<current_r-1e-12:
                            current_r=desired; stop=entry-current_r; bear_adj+=1
                    target=entry+2*current_r

                if lo<=stop:
                    add_trade(trades,policy,sym,entry,partial_price,stop,partial_done,'DIRECTIONAL_STOP',cost_bps,mfe,mae,initial_r,current_r,bull_adj,bear_adj,states)
                    pos=None; continue

                if (not partial_done) and hi>=target:
                    partial_done=True; partial_price=target

                if partial_done:
                    lower=x.at[i,'il']; lower_prev=x.at[i,'il_prev']; close5=x.at[i,'close5']; prev=x.at[i,'close_prev_dir']
                    if all(pd.notna(v) for v in (lower,lower_prev,close5,prev)) and float(prev)>=float(lower_prev) and float(close5)<float(lower):
                        add_trade(trades,policy,sym,entry,partial_price,p,True,'INNER_LOWER_05SIGMA_CROSS',cost_bps,mfe,mae,initial_r,current_r,bull_adj,bear_adj,states)
                        pos=None; continue

                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    add_trade(trades,policy,sym,entry,partial_price,p,partial_done,'SESSION_CLOSE',cost_bps,mfe,mae,initial_r,current_r,bull_adj,bear_adj,states)
                    pos=None; continue
                pos=(sym,ei,entry,initial_r,current_r,stop,target,partial_done,partial_price,mfe,mae,bull_adj,bear_adj,states)

        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=dbb_setup_row(x.loc[i])
                if not ok or not one_min_timing(x,i): continue
                if pd.isna(x.at[i,'iw']) or float(x.at[i,'iw'])<=0: continue
                entry=float(x.at[i,'close']); initial_r=float(x.at[i,'iw']); stop=entry-initial_r; target=entry+2*initial_r
                if stop<=0: continue
                cands.append((score,sym,i,entry,initial_r,stop,target))
            if cands:
                cands.sort(reverse=True); _,sym,i,entry,initial_r,stop,target=cands[0]
                pos=(sym,i,entry,initial_r,initial_r,stop,target,False,None,0.0,0.0,0,0,{})
    return trades


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--cost-bps',type=float,default=8.0); a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0); dates=sorted(bars['date_et'].unique())[-a.max_days:]; bars=bars[bars['date_et'].isin(dates)].copy()
    data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}; data=enrich_bands(data)
    print(f'V249 SOURCE={table} DAYS={len(dates)} BARS={len(bars)} ENTRY=V240D_ORIGINAL R=INNER_WIDTH EXIT=2R_HALF+INNER_LOWER',flush=True)
    results={}
    for name,cfg in POLICIES.items():
        tr=replay(data,a.cost_bps,name,cfg); m=metrics(tr); results[name]=m
        print(name+'_METRICS=',json.dumps(m,ensure_ascii=False),flush=True)
    ranked=sorted(results.items(),key=lambda kv:(kv[1].get('pf',0),kv[1].get('net_pct',-999),kv[1].get('max_dd_pct',-999)),reverse=True)
    print('RANKING=',json.dumps([{'policy':k,**v} for k,v in ranked],ensure_ascii=False),flush=True)
if __name__=='__main__': main()
