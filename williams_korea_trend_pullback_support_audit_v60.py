#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def rsi(vals,p=14):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    gains=[]; losses=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/p; al=sum(losses)/p
    out[p]=100 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,len(vals)):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-(100/(1+ag/al))
    return out

def ema(vals,p):
    if not vals:return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=80: out[d]=rows
    return out

def swing_lows(lows, look=3):
    idx=[]
    for i in range(look,len(lows)-look):
        x=lows[i]
        if all(x<=lows[j] for j in range(i-look,i)) and all(x<lows[j] for j in range(i+1,i+look+1)):
            idx.append(i)
    return idx

def classify_day(rows):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    e5=ema(c,5); e10=ema(c,10); e20=ema(c,20)
    r14=rsi(c,14)
    sl=swing_lows(l,3)
    events=[]
    for k in range(1,len(sl)):
        a,b=sl[k-1],sl[k]
        if b<=a+2: continue
        prev_low=l[a]; cur_low=l[b]
        higher_low=cur_low>=prev_low*0.998
        pre_hi=max(h[a:b+1])
        impulse=pct(prev_low,pre_hi)
        trend=(e5[b]>e10[b]>e20[b]) or (c[b]>e20[b] and e20[b]>e20[max(0,b-5)])
        support_dist=abs(pct(prev_low,cur_low))
        reclaim=False; rebound_i=None
        for j in range(b+1,min(len(rows),b+8)):
            bull=c[j]>float(rows[j][1])
            rsi_up=(r14[j] is not None and r14[j-1] is not None and r14[j]>r14[j-1])
            if bull and c[j]>c[b] and rsi_up:
                reclaim=True; rebound_i=j; break
        if not higher_low or impulse<0.8: continue
        ei=rebound_i if rebound_i is not None else min(b+1,len(rows)-1)
        mfe=max([pct(c[ei],x) for x in h[ei+1:]], default=0.0)
        mae=min([pct(c[ei],x) for x in l[ei+1:]], default=0.0)
        events.append(dict(a=a,b=b,ei=ei,trend=trend,reclaim=reclaim,impulse=impulse,support_dist=support_dist,mfe=mfe,mae=mae,rsi=(r14[b] if r14[b] is not None else 50),time=rows[b][0]))
    return events

def summarize(name,arr):
    if not arr:
        print(name,'N=0'); return
    n=len(arr)
    print(f"{name} N={n} MFE_AVG={statistics.fmean(x['mfe'] for x in arr):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in arr):.2f}% MFE>=1={100*sum(x['mfe']>=1 for x in arr)/n:.1f}% MFE>=3={100*sum(x['mfe']>=3 for x in arr)/n:.1f}% MFE>=5={100*sum(x['mfe']>=5 for x in arr)/n:.1f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days)
        for d,rows in dm.items():
            for e in classify_day(rows):
                e.update(symbol=s,date=d); obs.append(e)
    print('=== WILLIAMS KOREA TREND / PULLBACK / SUPPORT AUDIT V60 ===')
    print('Diagnostic only. No fixed exit. Detect higher-low pullbacks and rebound confirmation from price structure first.')
    summarize('ALL',obs)
    summarize('TREND',[x for x in obs if x['trend']])
    summarize('TREND+RECLAIM',[x for x in obs if x['trend'] and x['reclaim']])
    summarize('NO_RECLAIM',[x for x in obs if not x['reclaim']])
    print('--- TOP TREND+RECLAIM ---')
    top=sorted([x for x in obs if x['trend'] and x['reclaim']], key=lambda x:x['mfe'], reverse=True)[:40]
    for x in top:
        print(f"{x['date']} {x['symbol']} T={x['time']} IMP={x['impulse']:.2f}% SUPDIST={x['support_dist']:.2f}% RSI={x['rsi']:.1f} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}%")
    con.close()

if __name__=='__main__': main()
