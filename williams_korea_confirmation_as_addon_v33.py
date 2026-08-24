#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def rsi(vals,p):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    gs=[];ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,len(vals)):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

def summarize(name,arr):
    if not arr:
        print(name,'N=0'); return
    print(f"{name} N={len(arr)} MFE_AVG={statistics.fmean(x['mfe'] for x in arr):.2f}% STR5={100*sum(x['mfe']>=5 for x in arr)/len(arr):.1f}% MAE_AVG={statistics.fmean(x['mae'] for x in arr):.2f}% EOD_AVG={statistics.fmean(x['eod'] for x in arr):.2f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(closes,2)
            idx=None
            for i in range(3,len(cur)-25):
                if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1; entry=closes[ei]
            mfe=max(pct(entry,h) for h in highs[ei:]); mae=min(pct(entry,l) for l in lows[ei:]); eod=pct(entry,closes[-1])
            def ret(k):
                j=min(ei+k,len(closes)-1); return pct(entry,closes[j])
            r3=ret(3); r5=ret(5); r10=ret(10); r20=ret(20)
            # continuation confirmations are NOT entry gates here; they are post-entry hold/add-on states
            c3=r3>=0.3
            c5=r5>=0.5
            c10=r10>=1.0
            persistent=(c3 and c5) or (c5 and c10)
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,eod=eod,r3=r3,r5=r5,r10=r10,r20=r20,c3=c3,c5=c5,c10=c10,persistent=persistent))
    print('=== WILLIAMS KOREA CONFIRMATION AS ADDON V33 ===')
    print('ENTRY is always Williams next-bar. 3/5/10m continuation is evaluated only as HOLD/ADD-ON evidence, never as delayed entry gate.')
    summarize('ALL',obs)
    summarize('C3_HOLD',[x for x in obs if x['c3']])
    summarize('C5_HOLD',[x for x in obs if x['c5']])
    summarize('C10_HOLD',[x for x in obs if x['c10']])
    summarize('PERSISTENT_HOLD',[x for x in obs if x['persistent']])
    print('--- PERSISTENT ---')
    for x in obs:
        if x['persistent']:
            print(f"{x['d']} {x['s']} R3={x['r3']:.2f}% R5={x['r5']:.2f}% R10={x['r10']:.2f}% R20={x['r20']:.2f}% MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% EOD={x['eod']:.2f}%")
    con.close()

if __name__=='__main__': main()
