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
        if len(rows)>=30: out[d]=rows
    return out

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
            c=[float(r[4]) for r in cur]; h=[float(r[2]) for r in cur]; l=[float(r[3]) for r in cur]; v=[float(r[5]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-35):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1; entry=c[ei]
            mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:])
            def rr(k):
                j=min(ei+k,len(c)-1); return pct(entry,c[j])
            def maxr(k):
                end=min(len(h),ei+k+1); return max(pct(entry,x) for x in h[ei:end])
            def minr(k):
                end=min(len(l),ei+k+1); return min(pct(entry,x) for x in l[ei:end])
            def volr(k):
                j=min(len(v),ei+k)
                a=sum(v[ei:j])
                b=sum(v[max(ei-5,0):ei])
                return a/b if b>0 else 0.0
            first_ge1=first_ge2=first_ge3=None
            first_le_n05=first_le_n10=None
            for j in range(ei,len(c)):
                cr=pct(entry,c[j]); hr=pct(entry,h[j]); lr=pct(entry,l[j])
                if first_ge1 is None and hr>=1: first_ge1=j-ei
                if first_ge2 is None and hr>=2: first_ge2=j-ei
                if first_ge3 is None and hr>=3: first_ge3=j-ei
                if first_le_n05 is None and lr<=-0.5: first_le_n05=j-ei
                if first_le_n10 is None and lr<=-1.0: first_le_n10=j-ei
            obs.append(dict(
                d=d,s=s,mfe=mfe,mae=mae,
                r3=rr(3),r5=rr(5),r10=rr(10),r15=rr(15),r20=rr(20),r30=rr(30),
                m5=maxr(5),m10=maxr(10),m15=maxr(15),m20=maxr(20),m30=maxr(30),
                n5=minr(5),n10=minr(10),n15=minr(15),n20=minr(20),n30=minr(30),
                v5=volr(5),v10=volr(10),
                t1=first_ge1,t2=first_ge2,t3=first_ge3,tn05=first_le_n05,tn10=first_le_n10
            ))
    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; oos=set(dates[cut:])
    o=[x for x in obs if x['d'] in oos]
    print('=== WILLIAMS KOREA OOS PATH PROFILE V45 ===')
    print('Diagnostic only. Goal: distinguish true weak signals from latent runners without using future data for decisions.')
    print('OOS_DATES',','.join(sorted(oos)))
    groups=[
        ('WEAK_MFE<1',[x for x in o if x['mfe']<1]),
        ('MID_MFE1_3',[x for x in o if 1<=x['mfe']<3]),
        ('STRONG_MFE>=3',[x for x in o if x['mfe']>=3]),
        ('BIG_MFE>=5',[x for x in o if x['mfe']>=5]),
    ]
    for name,a in groups:
        if not a: continue
        print(f'--- {name} N={len(a)} ---')
        for f in ('r3','r5','r10','r15','r20','r30','m5','m10','m15','m20','m30','n5','n10','n15','n20','n30','v5','v10'):
            print(f'{f.upper()} AVG={statistics.fmean(x[f] for x in a):.3f} MED={statistics.median(x[f] for x in a):.3f}')
        for f in ('t1','t2','t3','tn05','tn10'):
            vals=[x[f] for x in a if x[f] is not None]
            print(f'{f.upper()} HIT={len(vals)}/{len(a)} AVG={(statistics.fmean(vals) if vals else -1):.1f}m MED={(statistics.median(vals) if vals else -1):.1f}m')
    print('--- OOS OBS ---')
    for x in o:
        print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% R3={x['r3']:.2f}% R5={x['r5']:.2f}% R10={x['r10']:.2f}% R15={x['r15']:.2f}% R20={x['r20']:.2f}% M5={x['m5']:.2f}% M10={x['m10']:.2f}% M20={x['m20']:.2f}% N5={x['n5']:.2f}% N10={x['n10']:.2f}% T1={x['t1']} T2={x['t2']} T3={x['t3']} TN05={x['tn05']} TN10={x['tn10']}")
    con.close()

if __name__=='__main__': main()
