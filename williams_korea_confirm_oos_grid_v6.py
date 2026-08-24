#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def rsi(vals,p=2):
    n=len(vals); out=[None]*n
    if n<p+2: return out
    gs=[]; ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=20: out[d]=rows
    return out

def raws(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-15):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes))
    return out

def metrics(xs):
    if not xs: return {'n':0,'avg':0,'win':0,'pf':0,'mdd':0}
    avg=statistics.fmean(xs); win=100*sum(x>0 for x in xs)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(xs),'avg':avg,'win':win,'pf':pf,'mdd':mdd}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    grids=[(4,0.20),(4,0.30),(4,0.40),(4,0.50),(5,0.10),(5,0.20),(5,0.30)]
    by={g:[] for g in grids}
    all_dates=set()
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]
            for i,closes in raws(dm[ds[di-1]],dm[d]):
                for g in grids:
                    m,t=g; j=i+m
                    if j<len(closes) and pct(closes[i],closes[j])>=t:
                        k=min(j+5,len(closes)-1)
                        by[g].append((d,pct(closes[j],closes[k]))); all_dates.add(d)
    dates=sorted(all_dates); split=len(dates)//2; isd=set(dates[:split]); oosd=set(dates[split:])
    print('=== WILLIAMS KOREA CONFIRM OOS GRID V6 ===')
    print('DATES=',dates,'IS=',len(isd),'OOS=',len(oosd))
    rows=[]
    for g in grids:
        rec=by[g]
        am=metrics([r for _,r in rec]); im=metrics([r for d,r in rec if d in isd]); om=metrics([r for d,r in rec if d in oosd])
        score=om['avg'] + min(om['pf'],3)*0.05 + om['mdd']*0.002 if om['n'] else -999
        print(f"{g[0]}m +{g[1]:.2f}% ALL N={am['n']} AVG={am['avg']:.4f}% PF={am['pf']:.3f} | IS N={im['n']} AVG={im['avg']:.4f}% PF={im['pf']:.3f} | OOS N={om['n']} AVG={om['avg']:.4f}% WIN={om['win']:.2f}% PF={om['pf']:.3f} MDD={om['mdd']:.4f}% SCORE={score:.4f}")
        rows.append((score,g,om))
    print('\n=== OOS RANK ===')
    for idx,(score,g,om) in enumerate(sorted(rows,reverse=True),1):
        print(f"{idx} {g[0]}m +{g[1]:.2f}% OOS_N={om['n']} AVG={om['avg']:.4f}% WIN={om['win']:.2f}% PF={om['pf']:.3f} MDD={om['mdd']:.4f}% SCORE={score:.4f}")
    con.close()
if __name__=='__main__': main()
