#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def ema(vals,p):
    if not vals:return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

def rsi(vals,p):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    gs=[]; ls=[]
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

def stats(name,arr):
    if not arr:
        print(name,'N=0'); return
    print(f"{name} N={len(arr)} MFE_AVG={statistics.fmean(x['mfe'] for x in arr):.2f}% "
          f"STR5={100*sum(x['mfe']>=5 for x in arr)/len(arr):.1f}% "
          f"MAE_AVG={statistics.fmean(x['mae'] for x in arr):.2f}% "
          f"R5_AVG={statistics.fmean(x['r5'] for x in arr):.2f}% "
          f"R10_AVG={statistics.fmean(x['r10'] for x in arr):.2f}% "
          f"R20_AVG={statistics.fmean(x['r20'] for x in arr):.2f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; vols=[float(r[5]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(closes,2)
            idx=None
            for i in range(3,len(cur)-25):
                if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1; entry=closes[ei]
            mfe=max(pct(entry,h) for h in highs[ei:]); mae=min(pct(entry,l) for l in lows[ei:])
            def rr(k):
                j=min(ei+k,len(closes)-1); return pct(entry,closes[j])
            r1=rr(1); r3=rr(3); r5=rr(5); r10=rr(10); r20=rr(20)
            max5=max(pct(entry,h) for h in highs[ei:min(len(highs),ei+6)])
            min5=min(pct(entry,l) for l in lows[ei:min(len(lows),ei+6)])
            max10=max(pct(entry,h) for h in highs[ei:min(len(highs),ei+11)])
            v0=sum(vols[max(0,idx-2):idx+1])
            v1=sum(vols[ei:min(len(vols),ei+3)])
            post_vol=(v1/v0) if v0>0 else 0
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,r1=r1,r3=r3,r5=r5,r10=r10,r20=r20,max5=max5,min5=min5,max10=max10,post_vol=post_vol))
    print('=== WILLIAMS KOREA POST-SIGNAL PATH V32 ===')
    print('No open-price filter. Tests only post-Williams causal continuation.')
    stats('BASE',obs)
    tests=[
      ('R1>0',[x for x in obs if x['r1']>0]),
      ('R3>0',[x for x in obs if x['r3']>0]),
      ('R3>=0.3',[x for x in obs if x['r3']>=0.3]),
      ('R5>=0.5',[x for x in obs if x['r5']>=0.5]),
      ('MAX5>=0.8',[x for x in obs if x['max5']>=0.8]),
      ('R3>=0.3&MIN5>-0.8',[x for x in obs if x['r3']>=0.3 and x['min5']>-0.8]),
      ('R5>=0.5&MIN5>-0.8',[x for x in obs if x['r5']>=0.5 and x['min5']>-0.8]),
      ('R3>=0.3&POSTVOL>=0.8',[x for x in obs if x['r3']>=0.3 and x['post_vol']>=0.8]),
    ]
    for name,arr in tests: stats(name,arr)
    print('--- OBS ---')
    for x in obs:
        print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% R1={x['r1']:.2f}% R3={x['r3']:.2f}% R5={x['r5']:.2f}% R10={x['r10']:.2f}% R20={x['r20']:.2f}% MAX5={x['max5']:.2f}% MIN5={x['min5']:.2f}% POSTVOL={x['post_vol']:.2f}")
    con.close()

if __name__=='__main__': main()
