#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars, metrics
from tools.v240d_precomputed_fast_validate import (
    prep_symbol, confirmed_swing_low, choose_stop, trade_metrics_append,
    rsi, macd, dbb_setup_row
)

SYMBOLS=("SOXL","SOXS")


def original_entry(x: pd.DataFrame, i: int):
    """Exact V240D entry path used by the 491-trade baseline."""
    ok,score=dbb_setup_row(x.loc[i])
    if not ok:
        return False,score
    sl=x['close'].iloc[max(0,i-40):i+1].reset_index(drop=True)
    if len(sl)<29:
        return False,score
    rr=rsi(sl,14); m,s,h=macd(sl)
    timing=((rr.iat[-1]>=rr.iat[-2]) or (h.iat[-1]>=h.iat[-2])) and \
           (m.iat[-1]>s.iat[-1] or (m.iat[-2]<=s.iat[-2] and m.iat[-1]>s.iat[-1])) and \
           not (rr.iat[-2]>=70>rr.iat[-1])
    return bool(timing),score


def band_break(r):
    if pd.isna(r.get('inner_u')):
        return False
    inner_hold=float(r['close5'])>=float(r['inner_u'])
    macd_bull=float(r['macd'])>=float(r['sig'])
    hist_up=float(r['hist'])>=float(r['hist_prev'])
    return bool((not inner_hold) and ((not macd_bull) or (not hist_up)))


def replay(data,fallback,max_swing,cost_bps,policy):
    trades=[]; pos=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if pos:
            sym,ei,entry,stop,initial_stop,peak=pos
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close']); peak=max(peak,p)
                exit_reason=None
                gain=p/entry-1.0; peak_gain=peak/entry-1.0
                r=x.loc[i]

                if p <= stop:
                    exit_reason='STOP'
                elif policy=='BAND_ONLY':
                    if band_break(r): exit_reason='INNER_BAND_TREND_BREAK'
                elif policy=='FIXED_1P':
                    if gain>=0.01: exit_reason='TAKE_1P'
                elif policy=='FIXED_1P5':
                    if gain>=0.015: exit_reason='TAKE_1P5'
                elif policy=='FIXED_2P':
                    if gain>=0.02: exit_reason='TAKE_2P'
                elif policy=='BE_AFTER_1P_BAND':
                    if peak_gain>=0.01 and stop < entry: stop=entry
                    if band_break(r): exit_reason='INNER_BAND_TREND_BREAK'
                elif policy=='LOCK_05_AFTER_1P5_BAND':
                    if peak_gain>=0.015:
                        stop=max(stop,entry*1.005)
                    if band_break(r): exit_reason='INNER_BAND_TREND_BREAK'
                elif policy=='TRAIL_075_AFTER_1P5':
                    if peak_gain>=0.015:
                        stop=max(stop,peak*(1.0-0.0075))
                    if p<=stop: exit_reason='TRAIL_075P'
                elif policy=='TAKE50_2P_BE_BAND':
                    # Approximation for ranking exit quality without split-equity bookkeeping:
                    # after +2%, protect remaining position at breakeven and continue band trend.
                    if peak_gain>=0.02 and stop < entry: stop=entry
                    if band_break(r): exit_reason='POST_2P_BAND_BREAK'

                et=pd.Timestamp(x.at[i,'et'])
                if exit_reason is None and et.hour==15 and et.minute>=59:
                    exit_reason='SESSION_CLOSE'
                if exit_reason:
                    trade_metrics_append(trades,'DBB_'+policy,sym,ei,i,entry,p,exit_reason,x['high'],x['low'],cost_bps)
                    pos=None
                    continue
                pos=(sym,ei,entry,stop,initial_stop,peak)

        if pos is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=original_entry(x,i)
                if not ok: continue
                p=float(x.at[i,'close'])
                sw=confirmed_swing_low(x['low'],i-1)
                stop=choose_stop(p,sw,fallback,max_swing)
                cands.append((score,sym,i,p,stop))
            if cands:
                cands.sort(reverse=True)
                _,sym,i,p,stop=cands[0]
                pos=(sym,i,p,stop,stop,p)
    return trades


def audit(trades):
    if not trades: return {}
    df=pd.DataFrame(trades)
    return {
        'trades':int(len(df)),
        'mfe_ge_1pct':int((df['mfe']>=.01).sum()),
        'mfe_ge_2pct':int((df['mfe']>=.02).sum()),
        'mfe_ge_1_but_loss':int(((df['mfe']>=.01)&(df['net_return']<0)).sum()),
        'mfe_ge_2_but_loss':int(((df['mfe']>=.02)&(df['net_return']<0)).sum()),
        'exit_reasons':df['exit_reason'].value_counts().to_dict(),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db')
    ap.add_argument('--max-days',type=int,default=135)
    ap.add_argument('--cost-bps',type=float,default=8.0)
    ap.add_argument('--fallback-risk-pct',type=float,default=0.015)
    ap.add_argument('--max-swing-risk-pct',type=float,default=0.025)
    a=ap.parse_args()
    bars,table=load_1m_bars(a.db,0)
    dates=sorted(bars['date_et'].unique())[-a.max_days:]
    bars=bars[bars['date_et'].isin(dates)].copy()
    data={s:prep_symbol(bars[bars['symbol']==s].copy()) for s in SYMBOLS}
    print(f'V246 SOURCE={table} DAYS={len(dates)} BARS={len(bars)}',flush=True)
    print('ENTRY=V240D_ORIGINAL EXPECT_BASELINE_TRADES_AROUND_491',flush=True)
    policies=('BAND_ONLY','FIXED_1P','FIXED_1P5','FIXED_2P','BE_AFTER_1P_BAND','LOCK_05_AFTER_1P5_BAND','TRAIL_075_AFTER_1P5','TAKE50_2P_BE_BAND')
    rows=[]
    for pol in policies:
        tr=replay(data,a.fallback_risk_pct,a.max_swing_risk_pct,a.cost_bps,pol)
        m=metrics(tr); au=audit(tr)
        print(pol+'_METRICS=',json.dumps(m),flush=True)
        print(pol+'_AUDIT=',json.dumps(au),flush=True)
        rows.append((pol,m))
    valid=[(p,m) for p,m in rows if m['trades']>=400]
    if valid:
        valid.sort(key=lambda z:(z[1]['pf'],z[1]['net_pct'],-abs(z[1]['max_dd_pct'])),reverse=True)
        print('BEST_EXIT_POLICY=',valid[0][0],json.dumps(valid[0][1]),flush=True)
    else:
        print('BEST_EXIT_POLICY=NONE_BASELINE_TRADE_COUNT_NOT_PRESERVED',flush=True)

if __name__=='__main__': main()
