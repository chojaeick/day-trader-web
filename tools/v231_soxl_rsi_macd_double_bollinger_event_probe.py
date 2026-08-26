#!/usr/bin/env python3
from __future__ import annotations
import argparse, sqlite3
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo
import pandas as pd

ROOT=Path('/home/ubuntu/day-trader-api')
DB=ROOT/'daytrader.db'
ET=ZoneInfo('America/New_York')


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def rsi(s, period=14):
    d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)
    au=up.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    ad=dn.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    rs=au/ad.replace(0,pd.NA)
    out=100-(100/(1+rs))
    return out.fillna(100).where(~((au==0)&(ad==0)),50)


def load_ticks(symbol, date_et):
    con=sqlite3.connect(DB)
    rows=con.execute("SELECT ts,price,qty,cum_volume FROM ticks WHERE symbol=? ORDER BY ts",(symbol.upper(),)).fetchall()
    con.close()
    if not rows: return pd.DataFrame()
    df=pd.DataFrame(rows,columns=['ts','price','qty','cum_volume'])
    df['ts']=pd.to_datetime(df['ts'],utc=True,errors='coerce'); df=df.dropna(subset=['ts','price'])
    df['et']=df['ts'].dt.tz_convert(ET)
    df=df[df['et'].dt.strftime('%Y-%m-%d')==date_et].copy()
    if df.empty:return df
    df=df.set_index('ts'); px=pd.to_numeric(df['price'],errors='coerce')
    ohlc=px.resample('5min').ohlc()
    qty=pd.to_numeric(df['qty'],errors='coerce').fillna(0).resample('5min').sum()
    b=ohlc.join(qty.rename('volume')).dropna(subset=['close']).reset_index().rename(columns={'ts':'time'})
    b['et']=b['time'].dt.tz_convert(ET)
    return b


def add_indicators(b):
    c=b['close'].astype(float)
    b['rsi14']=rsi(c,14)
    b['ema12']=ema(c,12); b['ema26']=ema(c,26); b['macd']=b['ema12']-b['ema26']; b['signal']=ema(b['macd'],9)
    b['mid']=c.rolling(20).mean(); b['sd']=c.rolling(20).std(ddof=0)
    b['inner_up']=b['mid']+0.5*b['sd']; b['inner_dn']=b['mid']-0.5*b['sd']
    b['outer_up']=b['mid']+3.0*b['sd']; b['outer_dn']=b['mid']-3.0*b['sd']
    for lvl in (30,50,70):
        b[f'rsi{lvl}_cross']=(b['rsi14'].shift(1)<lvl)&(b['rsi14']>=lvl)
    b['macd_gc']=(b['macd'].shift(1)<=b['signal'].shift(1))&(b['macd']>b['signal'])
    b['inner_break']=(b['close'].shift(1)<=b['inner_up'].shift(1))&(b['close']>b['inner_up'])
    b['outer_break']=(b['close'].shift(1)<=b['outer_up'].shift(1))&(b['close']>b['outer_up'])
    b['outer_loss']=(b['close'].shift(1)>=b['outer_up'].shift(1))&(b['close']<b['outer_up'])
    b['inner_loss']=(b['close'].shift(1)>=b['inner_up'].shift(1))&(b['close']<b['inner_up'])
    return b


def recent_swing_low(b, i, lookback=12):
    lo=max(0,i-lookback); w=b.iloc[lo:i]
    if len(w)<3:return None
    vals=w['low'].astype(float)
    # Prefer a local pivot low; otherwise the recent minimum is still a structural reference.
    piv=[]
    for j in range(1,len(vals)-1):
        if vals.iloc[j] <= vals.iloc[j-1] and vals.iloc[j] <= vals.iloc[j+1]: piv.append(float(vals.iloc[j]))
    return piv[-1] if piv else float(vals.min())


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbol',default='SOXL'); ap.add_argument('--date',default='2026-08-26')
    ap.add_argument('--window-bars',type=int,default=3,help='max 5m bars between the three entry events')
    ap.add_argument('--focus-start',default='15:30'); ap.add_argument('--focus-end',default='18:00')
    args=ap.parse_args()
    print('=== V231 RSI + MACD + DOUBLE BOLLINGER EVENT PROBE ===')
    print('READ_ONLY=YES ORDERS=NONE DB_MUTATION=NONE')
    print('RULES=5M RSI14 cross 30/50/70 + MACD(12,26,9) GC + BB20 inner=0.5sigma outer=3sigma')
    print('CONFIRM_WINDOW_BARS=',args.window_bars,'SYMBOL=',args.symbol,'DATE_ET=',args.date)
    b=load_ticks(args.symbol,args.date)
    if b.empty: raise SystemExit('NO_TICKS_FOR_DATE')
    b=add_indicators(b)
    times=b['et'].dt.strftime('%H:%M')
    focus=b[(times>=args.focus_start)&(times<=args.focus_end)].copy()
    print('BARS_TOTAL=',len(b),'FOCUS_BARS=',len(focus),'FIRST_ET=',b.iloc[0]['et'],'LAST_ET=',b.iloc[-1]['et'])

    events=[]
    for i,row in b.iterrows():
        rtypes=[]
        for lvl in (30,50,70):
            if bool(row[f'rsi{lvl}_cross']): rtypes.append(f'RSI{lvl}')
        if bool(row['macd_gc']): rtypes.append('MACD_GC')
        if bool(row['inner_break']): rtypes.append('INNER_BREAK')
        if bool(row['outer_break']): rtypes.append('OUTER_BREAK')
        if bool(row['outer_loss']): rtypes.append('OUTER_LOSS')
        if bool(row['inner_loss']): rtypes.append('INNER_LOSS')
        if rtypes:
            events.append((i,row['et'],float(row['close']),rtypes,float(row['rsi14']),float(row['macd']),float(row['signal']),float(row['inner_up']) if pd.notna(row['inner_up']) else None,float(row['outer_up']) if pd.notna(row['outer_up']) else None))
    print('\n=== EVENTS 15:30-18:00 ET ===')
    for e in events:
        if args.focus_start<=e[1].strftime('%H:%M')<=args.focus_end:
            print('ET',e[1].strftime('%H:%M'),'CLOSE',round(e[2],4),'EVENTS',e[3],'RSI',round(e[4],2),'MACD',round(e[5],4),'SIG',round(e[6],4),'INNER_UP',None if e[7] is None else round(e[7],4),'OUTER_UP',None if e[8] is None else round(e[8],4))

    # Detect a setup when one RSI upward cross, MACD GC, and inner-band break all occur within N bars.
    setups=[]; n=args.window_bars
    for i in range(len(b)):
        lo=max(0,i-n+1); w=b.iloc[lo:i+1]
        r_hits=[]
        for lvl in (30,50,70):
            inds=list(w.index[w[f'rsi{lvl}_cross']])
            if inds:r_hits.append((lvl,inds[-1]))
        mg=list(w.index[w['macd_gc']]); ib=list(w.index[w['inner_break']])
        if r_hits and mg and ib:
            last=max([x[1] for x in r_hits]+[mg[-1],ib[-1]])
            if last!=i: continue
            lvl=max(r_hits,key=lambda x:x[1])[0]
            entry=float(b.iloc[i]['close']); swing=recent_swing_low(b,i)
            stop=swing if swing is not None and swing<entry else entry*0.99
            if entry-stop > entry*0.03: stop=entry*0.99
            risk=entry-stop; target=entry+2*risk
            setups.append({'i':i,'et':b.iloc[i]['et'],'rsi_regime':lvl,'entry':entry,'stop':stop,'risk_pct':100*risk/entry,'target2r':target})
    # de-duplicate consecutive confirmations of same cluster
    ded=[]
    for s in setups:
        if ded and s['i']-ded[-1]['i']<=n: continue
        ded.append(s)
    print('\n=== SETUPS ===')
    if not ded: print('NONE')
    for s in ded:
        i=s['i']; future=b.iloc[i+1:].copy()
        maxp=float(future['high'].max()) if not future.empty else s['entry']; last=float(b.iloc[-1]['close'])
        target_hit=bool((future['high']>=s['target2r']).any()) if not future.empty else False
        stop_hit=bool((future['low']<=s['stop']).any()) if not future.empty else False
        outer_hit=bool(future['outer_break'].any()) if not future.empty else False
        inner_loss_after=bool(future['inner_loss'].any()) if not future.empty else False
        print('SETUP_ET=',s['et'].strftime('%Y-%m-%d %H:%M'),'RSI_REGIME=',s['rsi_regime'],'ENTRY=',round(s['entry'],4),'STOP=',round(s['stop'],4),'RISK_PCT=',round(s['risk_pct'],3),'TARGET_2R=',round(s['target2r'],4),'TARGET_HIT=',target_hit,'STOP_HIT=',stop_hit,'OUTER_BREAK_AFTER=',outer_hit,'INNER_LOSS_AFTER=',inner_loss_after,'MAX_AFTER=',round(maxp,4),'LAST=',round(last,4),'LAST_RET_PCT=',round((last/s['entry']-1)*100,3))

    print('\n=== FOCUS TABLE ===')
    cols=['et','close','rsi14','macd','signal','inner_up','outer_up','rsi30_cross','rsi50_cross','rsi70_cross','macd_gc','inner_break','outer_break','outer_loss','inner_loss']
    for _,r in focus[cols].iterrows():
        marks=[]
        for c in cols[7:]:
            if bool(r[c]): marks.append(c)
        if marks or ('15:50'<=r['et'].strftime('%H:%M')<='16:30'):
            print(r['et'].strftime('%H:%M'),'C',round(float(r['close']),4),'RSI',round(float(r['rsi14']),2),'M',round(float(r['macd']),4),'S',round(float(r['signal']),4),'IU',None if pd.isna(r['inner_up']) else round(float(r['inner_up']),4),'OU',None if pd.isna(r['outer_up']) else round(float(r['outer_up']),4),'MARK',marks)

if __name__=='__main__': main()
