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

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def classify_path(rows,ei):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    entry=c[ei]
    def ret(k):
        j=min(ei+k,len(c)-1); return pct(entry,c[j])
    def maxr(k):
        j=min(ei+k+1,len(h)); return max(pct(entry,x) for x in h[ei:j])
    def minr(k):
        j=min(ei+k+1,len(l)); return min(pct(entry,x) for x in l[ei:j])
    r5,r10,r15,r20=ret(5),ret(10),ret(15),ret(20)
    m5,m10,m15,m20=maxr(5),maxr(10),maxr(15),maxr(20)
    n5,n10,n15,n20=minr(5),minr(10),minr(15),minr(20)

    # Causal path classes derived from V45 OOS profile:
    # FAST_RUNNER: early expansion clearly present
    # LATENT_RUNNER: early dip/noise but recovery by 15-20m
    # WEAK: no meaningful expansion and persistent negative path
    # UNCERTAIN: neither side confirmed
    if m10>=2.0 or r5>=1.0 or r10>=1.0:
        cls='FAST_RUNNER'
    elif (m20>=2.0 and (r15>=0.5 or r20>=0.8)) or (m15>=1.5 and r15>0):
        cls='LATENT_RUNNER'
    elif m20<0.8 and (r10<0 or r20<0) and n10<=-0.5:
        cls='WEAK'
    else:
        cls='UNCERTAIN'
    return cls,dict(r5=r5,r10=r10,r15=r15,r20=r20,m5=m5,m10=m10,m15=m15,m20=m20,n5=n5,n10=n10,n15=n15,n20=n20)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='daytrader.db')
    ap.add_argument('--max-days',type=int,default=20)
    args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            c=[float(r[4]) for r in cur]; h=[float(r[2]) for r in cur]; l=[float(r[3]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-35):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1; entry=c[ei]
            mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:])
            cls,f=classify_path(cur,ei)
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,cls=cls,**f))
    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; oos=set(dates[cut:])
    o=[x for x in obs if x['d'] in oos]
    print('=== WILLIAMS KOREA CAUSAL PATH CLASSIFIER V46 ===')
    print('Diagnostic classifier only. Uses first 20m path; no future MFE in classification.')
    print('OOS_DATES',','.join(sorted(oos)))
    for cls in ('FAST_RUNNER','LATENT_RUNNER','WEAK','UNCERTAIN'):
        a=[x for x in o if x['cls']==cls]
        if not a:
            print(cls,'N=0'); continue
        print(f"{cls} N={len(a)} MFE_AVG={statistics.fmean(x['mfe'] for x in a):.2f}% MFE>=3={100*sum(x['mfe']>=3 for x in a)/len(a):.1f}% MFE>=5={100*sum(x['mfe']>=5 for x in a)/len(a):.1f}% MAE_AVG={statistics.fmean(x['mae'] for x in a):.2f}%")
        for x in a:
            print(f"  {x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% R5={x['r5']:.2f}% R10={x['r10']:.2f}% R15={x['r15']:.2f}% R20={x['r20']:.2f}% M10={x['m10']:.2f}% M20={x['m20']:.2f}% N10={x['n10']:.2f}%")
    con.close()

if __name__=='__main__': main()
