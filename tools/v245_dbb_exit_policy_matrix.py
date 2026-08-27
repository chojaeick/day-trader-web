#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars, metrics
from tools.v240d_precomputed_fast_validate import prep_symbol, confirmed_swing_low, choose_stop, trade_metrics_append, rsi, macd

SYMBOLS=("SOXL","SOXS")

def strict_entry(r):
    need=['rsi14','rsi_prev','macd','sig','hist','hist_prev','macd_prev','sig_prev','inner_u','inner_prev','close5','close_prev']
    if any(pd.isna(r.get(k)) for k in need): return False,0.0
    revent=any(float(r['rsi_prev']) <= lv < float(r['rsi14']) for lv in (30,50,70))
    macd_cross=float(r['macd_prev']) <= float(r['sig_prev']) and float(r['macd']) > float(r['sig'])
    inner_cross=float(r['close_prev']) <= float(r['inner_prev']) and float(r['close5']) > float(r['inner_u'])
    rsi_slope=float(r['rsi14'])-float(r['rsi_prev'])
    vr=0.0 if pd.isna(r.get('vol_ratio')) else float(r.get('vol_ratio'))
    boost=(rsi_slope >= 5.0) or (vr >= 1.5)
    score=float(revent)+float(macd_cross)+float(inner_cross)+float(rsi_slope>=5.0)+float(vr>=1.5)
    return bool(revent and macd_cross and inner_cross and boost),score

def one_min_veto(x,i):
    sl=x['close'].iloc[max(0,i-40):i+1].reset_index(drop=True)
    if len(sl)<29: return False
    rr=rsi(sl,14); m,s,h=macd(sl)
    if pd.isna(rr.iat[-1]) or pd.isna(rr.iat[-2]): return False
    falling_rsi=rr.iat[-1] < rr.iat[-2]
    falling_hist=h.iat[-1] < h.iat[-2]
    bearish_macd=m.iat[-1] < s.iat[-1]
    return bool(falling_rsi and falling_hist and bearish_macd)

def replay(data,fallback,max_swing,cost_bps,policy):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if pos:
            sym,ei,entry,stop,peak=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); peak=max(peak,p)
                exit_reason=None
                if p <= stop: exit_reason='INITIAL_STOP'
                else:
                    gain=p/entry-1.0; peak_gain=peak/entry-1.0
                    r=x.loc[i]
                    if policy=='FIXED_2P' and gain>=0.02: exit_reason='TAKE_2P'
                    elif policy=='BE_1P_THEN_BAND':
                        if peak_gain>=0.01 and stop < entry: stop=entry
                        if not pd.isna(r.get('inner_u')):
                            inner_hold=float(r['close5'])>=float(r['inner_u'])
                            macd_bull=float(r['macd'])>=float(r['sig'])
                            hist_up=float(r['hist'])>=float(r['hist_prev'])
                            if (not inner_hold) and ((not macd_bull) or (not hist_up)): exit_reason='INNER_BAND_TREND_BREAK'
                    elif policy=='TRAIL_AFTER_1P5':
                        if peak_gain>=0.015:
                            stop=max(stop, peak*(1.0-0.0075))
                        if p<=stop: exit_reason='TRAIL_075P'
                    elif policy=='BAND_ONLY':
                        if not pd.isna(r.get('inner_u')):
                            inner_hold=float(r['close5'])>=float(r['inner_u'])
                            macd_bull=float(r['macd'])>=float(r['sig'])
                            hist_up=float(r['hist'])>=float(r['hist_prev'])
                            if (not inner_hold) and ((not macd_bull) or (not hist_up)): exit_reason='INNER_BAND_TREND_BREAK'
                et=pd.Timestamp(x.at[i,'et'])
                if exit_reason is None and et.hour==15 and et.minute>=59: exit_reason='SESSION_CLOSE'
                if exit_reason:
                    trade_metrics_append(trades,'DBB_'+policy,sym,ei,i,entry,p,exit_reason,x['high'],x['low'],cost_bps)
                    pos=None
                    continue
                pos=(sym,ei,entry,stop,peak)
        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=strict_entry(x.loc[i])
                if not ok or one_min_veto(x,i): continue
                p=float(x.at[i,'close']); sw=confirmed_swing_low(x['low'],i-1); stop=choose_stop(p,sw,fallback,max_swing)
                cands.append((score,sym,i,p,stop))
            if cands:
                cands.sort(reverse=True)
                _,sym,i,p,stop=cands[0]; pos=(sym,i,p,stop,p)
    return trades

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--cost-bps',type=float,default=8.0); ap.add_argument('--fallback-risk-pct',type=float,default=0.015); ap.add_argument('--max-swing-risk-pct',type=float,default=0.025); a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0); dates=sorted(bars['date_et'].unique())[-a.max_days:]; bars=bars[bars['date_et'].isin(dates)].copy(); data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}
    print(f'V245 SOURCE={table} DAYS={len(dates)} BARS={len(bars)}',flush=True)
    for pol in ('BAND_ONLY','FIXED_2P','BE_1P_THEN_BAND','TRAIL_AFTER_1P5'):
        tr=replay(data,a.fallback_risk_pct,a.max_swing_risk_pct,a.cost_bps,pol); m=metrics(tr)
        print(pol+'_METRICS=',json.dumps(m),flush=True)
        if tr:
            df=pd.DataFrame(tr); print(pol+'_EXIT_REASONS=',json.dumps(df['exit_reason'].value_counts().to_dict()),flush=True)

if __name__=='__main__': main()
