#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']
HORIZONS=[1,3,5,10,20]


def ema(vals, span):
    if not vals: return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


def rsi_wilder(vals, period=14):
    n=len(vals); out=[None]*n
    if n<period+2: return out
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    for i in range(period+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    return out


def pct(a,b): return None if not a else (b/a-1.0)*100.0

def mean(xs):
    xs=[x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None

def win(xs):
    xs=[x for x in xs if x is not None]
    return 100.0*sum(1 for x in xs if x>0)/len(xs) if xs else None

def fmt(x): return 'NA' if x is None else f'{x:.4f}'


def cross_up(arr,i,level,lookback):
    start=max(1,i-lookback)
    return any(arr[j-1] is not None and arr[j] is not None and arr[j-1]<=level<arr[j] for j in range(start,i+1))

def pair_cross_up(a,b,i,lookback):
    start=max(1,i-lookback)
    return any(a[j-1]<=b[j-1] and a[j]>b[j] for j in range(start,i+1))


def local_lows(vals,start,end,window=2):
    out=[]
    for i in range(max(start,window),min(end,len(vals)-window)):
        v=vals[i]
        if all(v<=vals[j] for j in range(i-window,i+window+1) if j!=i): out.append(i)
    return out


def bullish_divergence(closes,rsi,i):
    start=max(0,i-59); lows=local_lows(closes,start,i+1,2)
    lows=[x for x in lows if rsi[x] is not None]
    if len(lows)<2: return False
    a,b=lows[-2],lows[-1]
    return closes[b]<=closes[a] and rsi[b]>rsi[a]+2.0


def fujimoto_entry_series(closes):
    rsi=rsi_wilder(closes,14)
    e12=ema(closes,12); e26=ema(closes,26)
    macd=[a-b for a,b in zip(e12,e26)]
    signal=ema(macd,9); hist=[a-b for a,b in zip(macd,signal)]
    qualify=[False]*len(closes); scores=[None]*len(closes); reason_counts=[0]*len(closes)
    for i in range(40,len(closes)):
        rv=rsi[i]
        if rv is None: continue
        score=20; reasons=0
        rsi30up=cross_up(rsi,i,30,5)
        rsi50up=cross_up(rsi,i,50,3)
        rsi_rising=all(rsi[k] is not None for k in (i,i-1,i-2)) and rsi[i]>rsi[i-1]>rsi[i-2]
        bull_div=bullish_divergence(closes,rsi,i)
        golden=pair_cross_up(macd,signal,i,3)
        hist_rising=i>=2 and hist[i]>hist[i-1]>hist[i-2]
        macd_zero_up=cross_up(macd,i,0,5)
        mv,sv,hv=macd[i],signal[i],hist[i]

        if rsi30up: score+=12; reasons+=1
        if rsi50up: score+=12; reasons+=1
        if rv>=50: score+=8
        if rsi_rising: score+=6; reasons+=1
        if bull_div: score+=12; reasons+=1
        if golden: score+=14; reasons+=1
        if mv>sv: score+=8
        if hist_rising: score+=8; reasons+=1
        if macd_zero_up: score+=10; reasons+=1
        if mv>0: score+=6
        if rv>=50 and mv>sv and hv>0: score+=10; reasons+=1

        # same penalties as current Fujimoto score v1
        rsi50down=any(rsi[j-1] is not None and rsi[j] is not None and rsi[j-1]>=50>rsi[j] for j in range(max(1,i-2),i+1))
        rsi70down=any(rsi[j-1] is not None and rsi[j] is not None and rsi[j-1]>=70>rsi[j] for j in range(max(1,i-2),i+1))
        dead=any(macd[j-1]>=signal[j-1] and macd[j]<signal[j] for j in range(max(1,i-2),i+1))
        hist_falling=i>=2 and hist[i]<hist[i-1]<hist[i-2]
        if rsi50down: score-=10
        if rsi70down: score-=12
        if dead: score-=15
        if hist_falling: score-=8
        score=max(0,min(100,int(round(score))))
        scores[i]=score; reason_counts[i]=reasons
        qualify[i]=(score>=80 and reasons>=3)
    return qualify,scores,reason_counts,rsi,macd,signal,hist


def load_days(con,symbol,max_days):
    dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days,)).fetchall()]
    dates=sorted(dates)
    out={}
    for d in dates:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out


def simulate_symbol(con,symbol,max_days):
    dm=load_days(con,symbol,max_days); events=[]
    for d,rows in dm.items():
        if len(rows)<60: continue
        closes=[float(r[4]) for r in rows]
        qualify,scores,rcount,rsi,macd,signal,hist=fujimoto_entry_series(closes)
        was=False
        for i in range(len(rows)):
            q=qualify[i]
            if q and not was:
                entry=closes[i]
                e={'symbol':symbol,'date':d,'time':str(rows[i][0]),'entry':entry,'score':scores[i],'reasons':rcount[i],'rsi':rsi[i],'macd':macd[i],'signal':signal[i],'hist':hist[i]}
                for h in HORIZONS:
                    e[f'r{h}']=pct(entry,closes[i+h]) if i+h<len(closes) else None
                future=rows[i+1:min(len(rows),i+21)]
                e['mfe20']=pct(entry,max(float(x[2]) for x in future)) if future else None
                e['mae20']=pct(entry,min(float(x[3]) for x in future)) if future else None
                events.append(e)
            was=q
    return events


def summarize(events):
    print('=== FUJIMOTO ENTRY-ONLY SIM V1-USA ===')
    print('RULE=current FUJIMOTO_SCORE_V1 + ENGINE_V1: score>=80 AND entry_reasons>=3; signal on false->true transition')
    print('SIGNALS=',len(events))
    if not events: return
    print('SYMBOLS=',len(set(e['symbol'] for e in events)),'DAYS=',len(set((e['symbol'],e['date']) for e in events)))
    for h in HORIZONS:
        vals=[e[f'r{h}'] for e in events if e[f'r{h}'] is not None]
        print(f'R{h}M_AVG={fmt(mean(vals))}% WIN={fmt(win(vals))}% N={len(vals)}')
    print('MFE20_AVG=',fmt(mean([e['mfe20'] for e in events]))+'%')
    print('MAE20_AVG=',fmt(mean([e['mae20'] for e in events]))+'%')
    print('=== BY SYMBOL ===')
    for s in sorted(set(e['symbol'] for e in events)):
        es=[e for e in events if e['symbol']==s]
        r5=[e['r5'] for e in es if e['r5'] is not None]; r20=[e['r20'] for e in es if e['r20'] is not None]
        print(s,'N=',len(es),'R5=',fmt(mean(r5)),'W5=',fmt(win(r5)),'R20=',fmt(mean(r20)),'W20=',fmt(win(r20)),'MFE=',fmt(mean([e['mfe20'] for e in es])),'MAE=',fmt(mean([e['mae20'] for e in es])))
    print('=== FIRST 20 SIGNALS ===')
    for e in events[:20]:
        print(e['symbol'],e['date'],e['time'],'ENTRY=',round(e['entry'],4),'SCORE=',e['score'],'REASONS=',e['reasons'],'RSI=',round(e['rsi'],2),'R5=',fmt(e['r5']),'R20=',fmt(e['r20']))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); all_events=[]
    for s in syms:
        n=con.execute("select count(*),count(distinct trade_date) from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR'",(s,)).fetchone()
        print('AUDIT',s,'ROWS=',n[0],'DAYS=',n[1])
        all_events.extend(simulate_symbol(con,s,args.max_days))
    con.close(); all_events.sort(key=lambda e:(e['date'],e['time'],e['symbol']))
    summarize(all_events)

if __name__=='__main__': main()
