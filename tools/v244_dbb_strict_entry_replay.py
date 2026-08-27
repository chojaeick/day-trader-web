#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, sys
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.v240d_precomputed_fast_validate import (
    SYMBOLS, load_1m_bars, prep_symbol, confirmed_swing_low, choose_stop,
    trade_metrics_append, rsi, macd
)
from tools.v240_validate_soxl_soxs_two_engines import metrics


def strict_setup_row(r: pd.Series):
    need=['rsi14','rsi_prev','macd','sig','hist','hist_prev','macd_prev','sig_prev','inner_u','inner_prev','close5','close_prev']
    if any(pd.isna(r.get(k)) for k in need):
        return False,0.0,{'reason':'INSUFFICIENT'}

    rsi_now=float(r['rsi14']); rsi_prev=float(r['rsi_prev'])
    rsi_event_level=None
    for lv in (30.0,50.0,70.0):
        if rsi_prev <= lv < rsi_now:
            rsi_event_level=lv
    rsi_slope=rsi_now-rsi_prev

    macd_now=float(r['macd']); sig_now=float(r['sig'])
    macd_prev=float(r['macd_prev']); sig_prev=float(r['sig_prev'])
    hist_now=float(r['hist']); hist_prev=float(r['hist_prev'])
    macd_cross = macd_prev <= sig_prev and macd_now > sig_now
    hist_accel = hist_now > hist_prev

    close_prev=float(r['close_prev']); inner_prev=float(r['inner_prev'])
    close_now=float(r['close5']); inner_now=float(r['inner_u'])
    fresh_inner_break = close_prev <= inner_prev and close_now > inner_now

    vol_ratio = 0.0 if pd.isna(r.get('vol_ratio')) else float(r.get('vol_ratio'))
    steep = rsi_slope >= 4.0
    volume_boost = vol_ratio >= 1.2
    boost = steep or volume_boost

    # Exact design intent: RSI event + fresh MACD confirmation + fresh inner-band break.
    # Momentum quality requires either steep RSI traversal or volume expansion.
    setup = bool(rsi_event_level is not None and macd_cross and fresh_inner_break and boost)
    score = float((rsi_event_level is not None) + macd_cross + fresh_inner_break + steep + volume_boost + hist_accel)
    return setup,score,{
        'rsi_event_level':rsi_event_level,'rsi_slope':rsi_slope,'steep':steep,
        'macd_cross':macd_cross,'hist_accel':hist_accel,'fresh_inner_break':fresh_inner_break,
        'vol_ratio':vol_ratio,'volume_boost':volume_boost
    }


def replay(data, fallback, max_swing, cost_bps):
    trades=[]; position=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if position:
            sym,ei,entry,stop=position
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close'])
                if p <= stop:
                    trade_metrics_append(trades,'DBB_STRICT',sym,ei,i,entry,p,'INITIAL_STOP',x['high'],x['low'],cost_bps); position=None; continue
                r=x.loc[i]
                if not pd.isna(r.get('inner_u')):
                    inner_hold=float(r['close5'])>=float(r['inner_u'])
                    macd_bull=float(r['macd'])>=float(r['sig'])
                    hist_up=float(r['hist'])>=float(r['hist_prev'])
                    if (not inner_hold) and ((not macd_bull) or (not hist_up)):
                        trade_metrics_append(trades,'DBB_STRICT',sym,ei,i,entry,p,'INNER_BAND_TREND_BREAK',x['high'],x['low'],cost_bps); position=None; continue
                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    trade_metrics_append(trades,'DBB_STRICT',sym,ei,i,entry,p,'SESSION_CLOSE',x['high'],x['low'],cost_bps); position=None; continue
        if position is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score,diag=strict_setup_row(x.loc[i])
                if not ok: continue
                # 1m is veto only: reject if both RSI and MACD histogram are rolling over.
                sl=x['close'].iloc[max(0,i-40):i+1].reset_index(drop=True)
                if len(sl)<29: continue
                rr=rsi(sl,14); m,s,h=macd(sl)
                rsi_down=rr.iat[-1] < rr.iat[-2]
                hist_down=h.iat[-1] < h.iat[-2]
                macd_bear=m.iat[-1] <= s.iat[-1]
                if (rsi_down and hist_down) or macd_bear:
                    continue
                p=float(x.at[i,'close']); sw=confirmed_swing_low(x['low'],i-1); stop=choose_stop(p,sw,fallback,max_swing)
                cands.append((score,sym,i,p,stop,diag))
            if cands:
                cands.sort(reverse=True)
                _,sym,i,p,stop,_=cands[0]
                position=(sym,i,p,stop)
    return trades


def audit(trades):
    d=pd.DataFrame(trades)
    if d.empty: return {'trades':0}
    return {
        'trades':len(d),
        'mfe_ge_1pct':int((d['mfe']>=.01).sum()),
        'mfe_ge_2pct':int((d['mfe']>=.02).sum()),
        'mfe_ge_1_but_loss':int(((d['mfe']>=.01)&(d['net_return']<0)).sum()),
        'mfe_ge_2_but_loss':int(((d['mfe']>=.02)&(d['net_return']<0)).sum()),
        'exit_reasons':d['exit_reason'].value_counts().to_dict(),
        'symbols':d['symbol'].value_counts().to_dict(),
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
    data={sym:prep_symbol(bars[bars['symbol']==sym].copy()) for sym in SYMBOLS}
    print(f'V244 SOURCE={table} DAYS={len(dates)} BARS={len(bars)}',flush=True)
    t=replay(data,a.fallback_risk_pct,a.max_swing_risk_pct,a.cost_bps)
    print('STRICT_DBB_METRICS=',json.dumps(metrics(t),ensure_ascii=False),flush=True)
    print('STRICT_DBB_AUDIT=',json.dumps(audit(t),ensure_ascii=False),flush=True)

if __name__=='__main__': main()
