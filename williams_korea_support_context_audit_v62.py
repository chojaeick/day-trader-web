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
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days,)).fetchall()]
    out={}
    for d in sorted(ds):
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=60: out[d]=rows
    return out

def swings(lows, highs, left=3, right=3):
    sl=[]; sh=[]
    n=len(lows)
    for i in range(left,n-right):
        if lows[i] == min(lows[i-left:i+right+1]): sl.append(i)
        if highs[i] == max(highs[i-left:i+right+1]): sh.append(i)
    return sl,sh

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days)
        for d,rows in dm.items():
            o=[float(r[1]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]; c=[float(r[4]) for r in rows]
            rr=rsi(c,14); sl,sh=swings(l,h)
            for k in range(1,len(sl)):
                a,b=sl[k-1],sl[k]
                if b-a<4: continue
                # Require a real upswing between lows, not merely two nearby lows.
                mids=[x for x in sh if a<x<b]
                if not mids: continue
                peak=max(h[x] for x in mids)
                impulse=pct(l[a],peak)
                hl=pct(l[a],l[b])
                # Higher-low tolerance: allow small undercut/reclaim, but reject large displacement.
                if hl < -0.8: continue
                # Search causal bullish reclaim shortly after second swing low.
                for j in range(b+1,min(b+9,len(rows)-1)):
                    bullish=c[j]>o[j]
                    rturn=(rr[j] is not None and rr[j-1] is not None and rr[j]>rr[j-1])
                    reclaim=pct(l[b],c[j])
                    if bullish and rturn and reclaim>=0.15:
                        # distance to prior swing low is contextual: too close can be weak/no impulse, too far may reflect strong HL.
                        prior_dist=abs(pct(l[a],l[b]))
                        mfe=max(pct(c[j],x) for x in h[j+1:]) if j+1<len(h) else 0
                        mae=min(pct(c[j],x) for x in l[j+1:]) if j+1<len(l) else 0
                        age=j-b
                        obs.append(dict(s=s,d=d,t=rows[j][0],imp=impulse,hl=hl,dist=prior_dist,reclaim=reclaim,age=age,rsi=rr[j] or 50,mfe=mfe,mae=mae))
                        break
    print('=== WILLIAMS KOREA SUPPORT CONTEXT AUDIT V62 ===')
    print('Question: when a rising structure pulls back, which higher-low/support contexts actually lead to continuation?')
    print('No trading exit. Structure-only diagnostic.')
    def show(name,a):
        if not a:
            print(name,'N=0'); return
        print(f"{name} N={len(a)} MFE_AVG={statistics.fmean(x['mfe'] for x in a):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in a):.2f}% MFE>=1={100*sum(x['mfe']>=1 for x in a)/len(a):.1f}% MFE>=3={100*sum(x['mfe']>=3 for x in a)/len(a):.1f}% MFE>=5={100*sum(x['mfe']>=5 for x in a)/len(a):.1f}%")
    show('ALL',obs)
    show('HL_FLAT[-0.8,0.5]',[x for x in obs if -0.8<=x['hl']<=0.5])
    show('HL_MODERATE(0.5,2]',[x for x in obs if 0.5<x['hl']<=2.0])
    show('HL_STRONG(2,5]',[x for x in obs if 2.0<x['hl']<=5.0])
    show('HL_EXTREME>5',[x for x in obs if x['hl']>5.0])
    show('IMPULSE>=2',[x for x in obs if x['imp']>=2])
    show('IMPULSE>=5',[x for x in obs if x['imp']>=5])
    show('FAST_RECLAIM<=2',[x for x in obs if x['age']<=2])
    show('HL0.5_5+IMP2+FAST',[x for x in obs if 0.5<x['hl']<=5 and x['imp']>=2 and x['age']<=2])
    print('--- TOP CASES ---')
    for x in sorted(obs,key=lambda z:z['mfe'],reverse=True)[:50]:
        print(f"{x['d']} {x['s']} T={x['t']} IMP={x['imp']:.2f}% HL={x['hl']:.2f}% RECLAIM={x['reclaim']:.2f}% AGE={x['age']} RSI={x['rsi']:.1f} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}%")
    con.close()
if __name__=='__main__': main()
