#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def rsi(vals,p=14):
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
        if len(rows)>=80: out[d]=rows
    return out

def swing_lows(lows,look=3):
    pts=[]
    for i in range(look,len(lows)-look):
        v=lows[i]
        if all(v<=lows[j] for j in range(i-look,i)) and all(v<lows[j] for j in range(i+1,i+look+1)):
            pts.append(i)
    return pts

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days)
        for d,rows in dm.items():
            c=[float(x[4]) for x in rows]; h=[float(x[2]) for x in rows]; l=[float(x[3]) for x in rows]; rr=rsi(c,14)
            sw=swing_lows(l,3)
            for k in range(1,len(sw)):
                p1,p2=sw[k-1],sw[k]
                if p2-p1<4: continue
                # Higher-low structure: current swing low not materially below previous low.
                higher_low=l[p2] >= l[p1]*0.997
                # Uptrend evidence prior to pullback: price made a higher high between lows.
                pre_high=max(h[p1:p2+1]); prior_high=max(h[max(0,p1-20):p1+1])
                uptrend=pre_high >= prior_high*1.003
                if not (higher_low and uptrend): continue
                # Search up to 10 bars after second swing low for bullish reclaim/reaction.
                for j in range(p2+1,min(p2+11,len(rows)-61)):
                    bullish=c[j]>c[j-1] and c[j]>float(rows[j][1])
                    rsi_turn=(rr[j] is not None and rr[j-1] is not None and rr[j]>rr[j-1])
                    reclaim=c[j] >= l[p2]*1.002
                    if bullish and rsi_turn and reclaim:
                        entry=c[j]
                        mfe=max(pct(entry,x) for x in h[j+1:]) if j+1<len(rows) else 0.0
                        mae=min(pct(entry,x) for x in l[j+1:]) if j+1<len(rows) else 0.0
                        support_dist=pct(l[p2],entry)
                        break_strength=pct(l[p1],l[p2])
                        obs.append(dict(s=s,d=d,t=rows[j][0],mfe=mfe,mae=mae,supd=support_dist,hl=break_strength,rsi=rr[j],age=j-p2))
                        break
    print('=== WILLIAMS KOREA TREND SUPPORT QUALITY V61 ===')
    print('Structure-first audit: uptrend -> pullback -> prior swing-low support -> bullish reclaim + RSI turn.')
    if not obs:
        print('N=0'); return
    def show(name,a):
        if not a: print(name,'N=0'); return
        print(f"{name} N={len(a)} MFE_AVG={statistics.fmean(x['mfe'] for x in a):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in a):.2f}% MFE>=1={100*sum(x['mfe']>=1 for x in a)/len(a):.1f}% MFE>=3={100*sum(x['mfe']>=3 for x in a)/len(a):.1f}% MFE>=5={100*sum(x['mfe']>=5 for x in a)/len(a):.1f}%")
    show('ALL',obs)
    show('SUPPORT_TIGHT',[x for x in obs if x['supd']<=0.8])
    show('SUPPORT_MED',[x for x in obs if 0.8<x['supd']<=1.5])
    show('SUPPORT_LOOSE',[x for x in obs if x['supd']>1.5])
    show('RECLAIM_FAST',[x for x in obs if x['age']<=3])
    show('RECLAIM_SLOW',[x for x in obs if x['age']>3])
    print('--- TOP CASES ---')
    for x in sorted(obs,key=lambda z:z['mfe'],reverse=True)[:40]:
        print(f"{x['d']} {x['s']} T={x['t']} SUPDIST={x['supd']:.2f}% HL={x['hl']:.2f}% RSI={x['rsi']:.1f} AGE={x['age']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}%")
    con.close()
if __name__=='__main__': main()
