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

def raw_signals(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]
    r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-65):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes,highs))
    return out

def metrics(xs):
    if not xs: return (0,0,0,0,0)
    avg=statistics.fmean(xs); win=100*sum(1 for x in xs if x>0)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return len(xs),avg,win,pf,mdd

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    grids=[(m,t) for m in (1,2,3,4,5) for t in (0.10,0.20,0.30,0.40,0.50)]
    rec={g:[] for g in grids}; count_raw=0
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            for i,closes,highs in raw_signals(dm[ds[di-1]],dm[ds[di]]):
                count_raw+=1
                for m,t in grids:
                    j=i+m
                    if j>=len(closes): continue
                    if pct(closes[i],closes[j]) >= t:
                        k=min(j+5,len(closes)-1)
                        rec[(m,t)].append(pct(closes[j],closes[k]))
    print('=== WILLIAMS KOREA CONFIRM GRID V4 ===')
    print('RAW_SIGNALS=',count_raw)
    for g in grids:
        n,avg,win,pf,mdd=metrics(rec[g])
        rate=100*n/count_raw if count_raw else 0
        print(f'CONFIRM {g[0]}m +{g[1]:.2f}% N={n} RATE={rate:.2f}% POST5_AVG={avg:.4f}% WIN={win:.2f}% PF={pf:.3f} MDD={mdd:.4f}%')
    ranked=[]
    for g in grids:
        n,avg,win,pf,mdd=metrics(rec[g])
        if n>=10:
            score=avg + min(pf,3)*0.05 + mdd*0.002
            ranked.append((score,g,n,avg,win,pf,mdd))
    print('\n=== RANK ===')
    for idx,x in enumerate(sorted(ranked,reverse=True),1):
        score,g,n,avg,win,pf,mdd=x
        print(f'{idx} {g[0]}m +{g[1]:.2f}% N={n} AVG={avg:.4f}% WIN={win:.2f}% PF={pf:.3f} MDD={mdd:.4f}% SCORE={score:.4f}')
    con.close()
if __name__=='__main__': main()
