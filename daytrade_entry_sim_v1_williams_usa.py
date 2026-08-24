#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

DB_DEFAULT='daytrader.db'
DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']
HORIZONS=[1,3,5,10,20]


def rsi2(closes):
    out=[None]*len(closes)
    if len(closes)<3: return out
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0.0)); losses.append(max(-d,0.0))
    for i in range(2,len(closes)):
        g=(gains[i-2]+gains[i-1])/2.0
        l=(losses[i-2]+losses[i-1])/2.0
        if l==0:
            out[i]=100.0 if g>0 else 50.0
        else:
            rs=g/l; out[i]=100.0-(100.0/(1.0+rs))
    return out


def pct(a,b):
    return (b/a-1.0)*100.0 if a else None


def mean(xs):
    xs=[x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def fmt(x):
    return '-' if x is None else f'{x:.4f}'


def regular_rows(conn,symbol,days):
    q='''SELECT trade_date, et_time, open, high, low, close, volume
         FROM historical_minute_bars
         WHERE symbol=? AND interval_min=1 AND session='REGULAR'
         ORDER BY trade_date, et_time'''
    rows=conn.execute(q,(symbol,)).fetchall()
    by=defaultdict(list)
    for r in rows: by[str(r[0])].append(r)
    dates=sorted(by)
    if days>0: dates=dates[-(days+1):]  # one extra day for previous range
    return {d:by[d] for d in dates}


def simulate_symbol(conn,symbol,max_days):
    by=regular_rows(conn,symbol,max_days)
    dates=sorted(by)
    events=[]
    for di in range(1,len(dates)):
        prev=by[dates[di-1]]; cur=by[dates[di]]
        if len(prev)<100 or len(cur)<30: continue
        prev_high=max(float(r[3]) for r in prev)
        prev_low=min(float(r[4]) for r in prev)
        day_open=float(cur[0][2])
        trigger=day_open+0.5*(prev_high-prev_low)
        closes=[float(r[5]) for r in cur]
        rsis=rsi2(closes)
        # Treat each fresh below->above crossing as a separate signal; same sustained crossing is not duplicated.
        for i in range(1,len(cur)):
            pc=closes[i-1]; cc=closes[i]
            cross=(pc<=trigger and cc>trigger)
            if not cross or rsis[i] is None or rsis[i] <= 50: continue
            e={'symbol':symbol,'date':dates[di],'time':str(cur[i][1]),'entry':cc,'trigger':trigger,'rsi2':rsis[i]}
            for h in HORIZONS:
                j=i+h
                e[f'r{h}']=pct(cc,closes[j]) if j<len(cur) else None
            j2=min(len(cur),i+21)
            highs=[float(cur[j][3]) for j in range(i+1,j2)]
            lows=[float(cur[j][4]) for j in range(i+1,j2)]
            e['mfe20']=pct(cc,max(highs)) if highs else None
            e['mae20']=pct(cc,min(lows)) if lows else None
            events.append(e)
    return events


def summarize(events):
    print('=== WILLIAMS ENTRY-ONLY SIM V1-USA ===')
    print('RULE=CrossUp(close, day_open + 0.5*(prev_day_high-prev_day_low)) AND RSI(2)>50')
    print('SIGNALS=',len(events))
    if not events:
        print('RESULT=NO_SIGNALS')
        return
    print('SYMBOLS=',len(set(e['symbol'] for e in events)),'DAYS=',len(set((e['symbol'],e['date']) for e in events)))
    for h in HORIZONS:
        vals=[e[f'r{h}'] for e in events if e[f'r{h}'] is not None]
        win=(sum(1 for x in vals if x>0)/len(vals)*100.0) if vals else None
        print(f'R{h}M_AVG={fmt(mean(vals))}% WIN={fmt(win)}% N={len(vals)}')
    print(f'MFE20_AVG={fmt(mean([e["mfe20"] for e in events]))}%')
    print(f'MAE20_AVG={fmt(mean([e["mae20"] for e in events]))}%')
    print('=== BY SYMBOL ===')
    for s in sorted(set(e['symbol'] for e in events)):
        es=[e for e in events if e['symbol']==s]
        r5=[e['r5'] for e in es if e['r5'] is not None]
        r20=[e['r20'] for e in es if e['r20'] is not None]
        w5=(sum(x>0 for x in r5)/len(r5)*100) if r5 else None
        print(s,'N=',len(es),'R5=',fmt(mean(r5)),'W5=',fmt(w5),'R20=',fmt(mean(r20)),'MFE=',fmt(mean([e['mfe20'] for e in es])),'MAE=',fmt(mean([e['mae20'] for e in es])))
    print('=== FIRST 20 SIGNALS ===')
    for e in events[:20]:
        print(e['symbol'],e['date'],e['time'],'ENTRY=',round(e['entry'],4),'TRIG=',round(e['trigger'],4),'RSI2=',round(e['rsi2'],2),'R5=',fmt(e['r5']),'R20=',fmt(e['r20']))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default=DB_DEFAULT)
    ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS))
    ap.add_argument('--max-days',type=int,default=20)
    args=ap.parse_args()
    syms=[s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    conn=sqlite3.connect(args.db)
    print('DB=',args.db)
    print('SYMBOLS=',','.join(syms))
    print('MAX_DAYS=',args.max_days)
    all_events=[]
    for s in syms:
        n=conn.execute("SELECT COUNT(*),COUNT(DISTINCT trade_date),MIN(trade_date),MAX(trade_date) FROM historical_minute_bars WHERE symbol=? AND interval_min=1 AND session='REGULAR'",(s,)).fetchone()
        print('AUDIT',s,'ROWS=',n[0],'DAYS=',n[1],'RANGE=',(n[2],n[3]))
        all_events.extend(simulate_symbol(conn,s,args.max_days))
    conn.close()
    all_events.sort(key=lambda e:(e['date'],e['time'],e['symbol']))
    summarize(all_events)

if __name__=='__main__': main()
