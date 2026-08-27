#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.v240_validate_soxl_soxs_two_engines import load_1m_bars, metrics, williams_daily

SYMBOLS = ("SOXL", "SOXS")


def rsi(s: pd.Series, period: int) -> pd.Series:
    d = s.astype(float).diff()
    gain = d.clip(lower=0.0)
    loss = -d.clip(upper=0.0)
    ag = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs = ag / al.mask(al == 0.0, np.nan)
    return (100.0 - 100.0/(1.0+rs)).fillna(100.0)


def macd(s: pd.Series, fast=12, slow=26, signal=9):
    m = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sig = m.ewm(span=signal, adjust=False).mean()
    return m, sig, m-sig


def confirmed_swing_low(lows: pd.Series, i: int, left=2, right=2, lookback=120):
    end = i-right
    start = max(left, end-lookback)
    found = None
    for j in range(start, end+1):
        v = lows.iat[j]
        if v < lows.iloc[j-left:j].min() and v <= lows.iloc[j+1:j+1+right].min():
            found = float(v)
    return found


def choose_stop(entry, swing, fallback=0.015, max_swing=0.025):
    if swing is not None:
        rp = (entry-swing)/entry
        if 0 < rp <= max_swing:
            return swing
    return entry*(1.0-fallback)


def prep_symbol(df: pd.DataFrame):
    x = df.sort_values("et").reset_index(drop=True).copy()
    x["rsi2"] = rsi(x["close"], 2)

    # completed 5m bars only; map completed values forward to 1m rows causally
    q = x.set_index(pd.to_datetime(x["et"]))
    b5 = q.resample("5min", label="left", closed="left").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna(subset=["open","high","low","close"]).reset_index(names="bucket")
    b5["rsi14"] = rsi(b5["close"], 14)
    m,s,h = macd(b5["close"])
    b5["macd"] = m; b5["sig"] = s; b5["hist"] = h
    mid = b5["close"].rolling(20).mean(); std=b5["close"].rolling(20).std(ddof=0)
    b5["inner_u"] = mid + 0.5*std
    b5["outer_u"] = mid + 3.0*std
    b5["vol_avg20"] = b5["volume"].shift(1).rolling(20).mean()
    b5["vol_ratio"] = b5["volume"] / b5["vol_avg20"]
    b5["rsi_prev"] = b5["rsi14"].shift(1)
    b5["hist_prev"] = b5["hist"].shift(1)
    b5["macd_prev"] = b5["macd"].shift(1)
    b5["sig_prev"] = b5["sig"].shift(1)
    b5["inner_prev"] = b5["inner_u"].shift(1)
    b5["close_prev"] = b5["close"].shift(1)

    t = pd.to_datetime(x["et"])
    x["bucket"] = t.dt.floor("5min") - pd.Timedelta(minutes=5)
    usecols = ["bucket","rsi14","rsi_prev","macd","sig","hist","hist_prev","macd_prev","sig_prev","inner_u","outer_u","inner_prev","close_prev","close","volume","vol_ratio"]
    mrg = b5[usecols].rename(columns={"close":"close5","volume":"volume5"})
    x = x.merge(mrg, on="bucket", how="left")
    return x


def trade_metrics_append(trades, engine, sym, ent_i, ext_i, entry, exitp, reason, highs, lows, cost_bps):
    seg_h = highs.iloc[ent_i:ext_i+1]
    seg_l = lows.iloc[ent_i:ext_i+1]
    mfe = float(seg_h.max()/entry - 1.0) if not seg_h.empty else 0.0
    mae = float(seg_l.min()/entry - 1.0) if not seg_l.empty else 0.0
    gross = exitp/entry - 1.0
    net = gross - 2*cost_bps/10000.0
    trades.append({"engine":engine,"symbol":sym,"entry_price":entry,"exit_price":exitp,
                   "gross_return":gross,"net_return":net,"mfe":mfe,"mae":mae,"exit_reason":reason})


def replay_williams(data, fallback, max_swing, cost_bps):
    trades=[]
    position=None
    daily={sym:williams_daily(df) for sym,df in data.items()}
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if position:
            sym,ei,entry,stop = position
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; price=float(x.at[i,'close'])
                sw=confirmed_swing_low(x['low'], i)
                if sw is not None and entry < sw < price and sw > stop:
                    stop=sw; position=(sym,ei,entry,stop)
                if price <= stop or pd.Timestamp(x.at[i,'et']).hour==15 and pd.Timestamp(x.at[i,'et']).minute>=59:
                    trade_metrics_append(trades,'WILLIAMS',sym,ei,i,entry,price,'STOP_OR_SESSION',x['high'],x['low'],cost_bps)
                    position=None
                    continue
        if position is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<1: continue
                d=daily[sym].get(x.at[i,'date_et'])
                if not d: continue
                trigger=float(d['session_open'])+0.5*(float(d['prev_high'])-float(d['prev_low']))
                pc=float(x.at[i-1,'close']); p=float(x.at[i,'close'])
                if pc <= trigger < p and float(x.at[i,'rsi2'])>50:
                    sw=confirmed_swing_low(x['low'], i-1)
                    stop=choose_stop(p,sw,fallback,max_swing)
                    cands.append((sym,i,p,stop))
            if cands:
                position=cands[0]
    return trades


def dbb_setup_row(r):
    vals=[r.get('rsi14'),r.get('rsi_prev'),r.get('macd'),r.get('sig'),r.get('hist'),r.get('hist_prev'),r.get('inner_u'),r.get('inner_prev'),r.get('close5'),r.get('close_prev')]
    if any(pd.isna(v) for v in vals): return False,0.0
    revent = any(float(r['rsi_prev']) <= lv < float(r['rsi14']) for lv in (30,50,70))
    macd_cross = float(r['macd_prev']) <= float(r['sig_prev']) and float(r['macd']) > float(r['sig'])
    macd_bull = float(r['macd']) > float(r['sig']) and float(r['hist']) >= float(r['hist_prev'])
    inner = (float(r['close_prev']) <= float(r['inner_prev']) and float(r['close5']) > float(r['inner_u'])) or float(r['close5']) > float(r['inner_u'])
    score = float(revent)+float(float(r['rsi14'])>float(r['rsi_prev']))+float(macd_cross or macd_bull)+float(float(r['hist'])>float(r['hist_prev']))+float(inner)+float((0 if pd.isna(r.get('vol_ratio')) else r['vol_ratio'])>=1.5)
    return bool(revent and (macd_cross or macd_bull) and inner),score


def replay_dbb(data, fallback, max_swing, cost_bps):
    trades=[]; position=None
    all_times=sorted(set().union(*[set(pd.to_datetime(df['et'])) for df in data.values()]))
    maps={sym:{pd.Timestamp(t):i for i,t in enumerate(pd.to_datetime(df['et']))} for sym,df in data.items()}
    for t in all_times:
        if position:
            sym,ei,entry,stop=position
            if t in maps[sym]:
                i=maps[sym][t]; x=data[sym]; p=float(x.at[i,'close'])
                if p <= stop:
                    trade_metrics_append(trades,'DOUBLE_BOLLINGER',sym,ei,i,entry,p,'INITIAL_STOP',x['high'],x['low'],cost_bps); position=None; continue
                r=x.loc[i]
                if not pd.isna(r.get('inner_u')):
                    inner_hold=float(r['close5'])>=float(r['inner_u'])
                    macd_bull=float(r['macd'])>=float(r['sig'])
                    hist_up=float(r['hist'])>=float(r['hist_prev'])
                    if (not inner_hold) and ((not macd_bull) or (not hist_up)):
                        trade_metrics_append(trades,'DOUBLE_BOLLINGER',sym,ei,i,entry,p,'INNER_BAND_TREND_BREAK',x['high'],x['low'],cost_bps); position=None; continue
                et=pd.Timestamp(x.at[i,'et'])
                if et.hour==15 and et.minute>=59:
                    trade_metrics_append(trades,'DOUBLE_BOLLINGER',sym,ei,i,entry,p,'SESSION_CLOSE',x['high'],x['low'],cost_bps); position=None; continue
        if position is None:
            cands=[]
            for sym in SYMBOLS:
                if t not in maps[sym]: continue
                i=maps[sym][t]; x=data[sym]
                if i<30: continue
                ok,score=dbb_setup_row(x.loc[i])
                if not ok: continue
                # 1m timing: current RSI rising or 1m MACD hist rising; precompute ad hoc on short rolling slice
                sl=x['close'].iloc[max(0,i-40):i+1]
                rr=rsi(sl.reset_index(drop=True),14); m,s,h=macd(sl.reset_index(drop=True))
                if len(sl)<29: continue
                timing=((rr.iat[-1]>=rr.iat[-2]) or (h.iat[-1]>=h.iat[-2])) and (m.iat[-1]>s.iat[-1] or (m.iat[-2]<=s.iat[-2] and m.iat[-1]>s.iat[-1])) and not (rr.iat[-2]>=70>rr.iat[-1])
                if not timing: continue
                p=float(x.at[i,'close']); sw=confirmed_swing_low(x['low'],i-1); stop=choose_stop(p,sw,fallback,max_swing)
                cands.append((score,sym,i,p,stop))
            if cands:
                cands.sort(reverse=True)
                _,sym,i,p,stop=cands[0]; position=(sym,i,p,stop)
    return trades


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='/home/ubuntu/day-trader-api/daytrader.db')
    ap.add_argument('--max-days',type=int,default=135)
    ap.add_argument('--cost-bps',type=float,default=8.0)
    ap.add_argument('--fallback-risk-pct',type=float,default=0.015)
    ap.add_argument('--max-swing-risk-pct',type=float,default=0.025)
    args=ap.parse_args()
    bars,table=load_1m_bars(args.db,0)
    dates=sorted(bars['date_et'].unique())[-args.max_days:]
    bars=bars[bars['date_et'].isin(dates)].copy()
    data={sym:prep_symbol(bars[bars['symbol']==sym].copy()) for sym in SYMBOLS}
    print(f'V240D SOURCE={table} DAYS={len(dates)} BARS={len(bars)}',flush=True)
    wt=replay_williams(data,args.fallback_risk_pct,args.max_swing_risk_pct,args.cost_bps)
    wm=metrics(wt); print('WILLIAMS_METRICS=',json.dumps(wm),flush=True)
    dt=replay_dbb(data,args.fallback_risk_pct,args.max_swing_risk_pct,args.cost_bps)
    dm=metrics(dt); print('DOUBLE_BOLLINGER_METRICS=',json.dumps(dm),flush=True)
    def eligible(m): return m['trades']>=20 and m['pf']>1 and m['net_pct']>0
    if eligible(wm) and not eligible(dm): w='WILLIAMS'
    elif eligible(dm) and not eligible(wm): w='DOUBLE_BOLLINGER'
    elif eligible(wm) and eligible(dm): w='WILLIAMS' if (wm['pf'],wm['net_pct'])>(dm['pf'],dm['net_pct']) else 'DOUBLE_BOLLINGER'
    else: w='NONE_CORE_NOT_READY'
    print('WINNER_PRELIMINARY=',w,flush=True)

if __name__=='__main__': main()
