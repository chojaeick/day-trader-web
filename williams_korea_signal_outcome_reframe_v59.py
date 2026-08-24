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

def horizon_stats(rows,si):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    entry=c[si]
    out={}
    for k in (1,2,3,5,10,20,30,60):
        j=min(si+k,len(rows)-1)
        seg_h=h[si+1:j+1] if j>si else []
        seg_l=l[si+1:j+1] if j>si else []
        out[f'R{k}']=pct(entry,c[j])
        out[f'M{k}']=max([pct(entry,x) for x in seg_h], default=0.0)
        out[f'N{k}']=min([pct(entry,x) for x in seg_l], default=0.0)
    seg_h=h[si+1:]; seg_l=l[si+1:]
    out['MEOD']=max([pct(entry,x) for x in seg_h], default=0.0)
    out['NEOD']=min([pct(entry,x) for x in seg_l], default=0.0)
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
            c=[float(r[4]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); si=None
            for i in range(3,len(cur)-61):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    si=i; break
            if si is None: continue
            st=horizon_stats(cur,si)
            obs.append(dict(d=d,s=s,si=si,**st))
    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; isd=set(dates[:cut]); oosd=set(dates[cut:])
    print('=== WILLIAMS KOREA SIGNAL OUTCOME REFRAME V59 ===')
    print('Question 1: does the Williams signal itself produce an immediate upward move?')
    print('Question 2: how often does that move persist into larger momentum?')
    print('No exit policy in this script. Future-only highs exclude the signal bar.')
    print('IS_DATES',','.join(sorted(isd))); print('OOS_DATES',','.join(sorted(oosd)))
    for scope,name in [(obs,'ALL'),([x for x in obs if x['d'] in oosd],'OOS')]:
        print('---',name,'---')
        n=len(scope)
        print(f'N={n}')
        for k in (1,2,3,5,10,20,30,60):
            up=sum(x[f'M{k}']>0 for x in scope)
            cpos=sum(x[f'R{k}']>0 for x in scope)
            ge03=sum(x[f'M{k}']>=0.3 for x in scope)
            ge05=sum(x[f'M{k}']>=0.5 for x in scope)
            print(f'H{k} FUTURE_UP={100*up/n:.1f}% CLOSE_POS={100*cpos/n:.1f}% MFE>=0.3={100*ge03/n:.1f}% MFE>=0.5={100*ge05/n:.1f}% MFE_AVG={statistics.fmean(x[f"M{k}"] for x in scope):.3f}% MAE_AVG={statistics.fmean(x[f"N{k}"] for x in scope):.3f}%')
        print(f'PERSIST_MFE>=1={100*sum(x["MEOD"]>=1 for x in scope)/n:.1f}% >=3={100*sum(x["MEOD"]>=3 for x in scope)/n:.1f}% >=5={100*sum(x["MEOD"]>=5 for x in scope)/n:.1f}% MEOD_AVG={statistics.fmean(x["MEOD"] for x in scope):.3f}%')
    print('--- OOS OBS ---')
    for x in obs:
        if x['d'] in oosd:
            print(f"{x['d']} {x['s']} M1={x['M1']:.2f}% N1={x['N1']:.2f}% R1={x['R1']:.2f}% M3={x['M3']:.2f}% R3={x['R3']:.2f}% M5={x['M5']:.2f}% R5={x['R5']:.2f}% MEOD={x['MEOD']:.2f}% NEOD={x['NEOD']:.2f}%")
    con.close()

if __name__=='__main__': main()
