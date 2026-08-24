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

def cci(h,l,c,p=20):
    out=[None]*len(c); tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    for i in range(p-1,len(c)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def pct(a,b): return (b/a-1)*100 if a else 0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

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
            r2=rsi(closes,2); r14=rsi(closes,14); cc=cci(highs,lows,closes,20)
            e12=ema(closes,12); e26=ema(closes,26); mac=[e12[i]-e26[i] for i in range(len(closes))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(mac))]
            idx=None
            for i in range(22,len(cur)-2):
                if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=min(idx+1,len(cur)-1); entry=closes[ei]
            mfe=max(pct(entry,h) for h in highs[ei:]); mae=min(pct(entry,l) for l in lows[ei:])
            ret1=pct(closes[idx-1],closes[idx]); ret3=pct(closes[idx-3],closes[idx]); ret5=pct(closes[idx-5],closes[idx])
            acc=ret1-(pct(closes[idx-3],closes[idx-1])/2)
            h1=hist[idx]-hist[idx-1]; h3=hist[idx]-hist[idx-3]
            r14s3=(r14[idx]-r14[idx-3]) if r14[idx] is not None and r14[idx-3] is not None else 0
            ccis3=(cc[idx]-cc[idx-3]) if cc[idx] is not None and cc[idx-3] is not None else 0
            vnow=sum(vols[idx-2:idx+1]); vprev=sum(vols[idx-5:idx-2]); vr=vnow/vprev if vprev>0 else 999
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,ret1=ret1,ret3=ret3,ret5=ret5,acc=acc,h1=h1,h3=h3,r14=r14s3,cci=ccis3,vr=vr))
    strong=[x for x in obs if x['mfe']>=5]
    weak=[x for x in obs if x['mfe']<1]
    mid=[x for x in obs if 1<=x['mfe']<5]
    feats=['ret1','ret3','ret5','acc','h1','h3','r14','cci','vr']
    print('=== WILLIAMS KOREA STRONG VS WEAK DISCRIMINANT V31 ===')
    print(f'BASE N={len(obs)} STRONG5={len(strong)} WEAK1={len(weak)} MID={len(mid)}')
    for f in feats:
        def med(a): return statistics.median(x[f] for x in a) if a else 0
        def avg(a): return statistics.fmean(x[f] for x in a) if a else 0
        print(f'{f.upper()} STRONG_AVG={avg(strong):.3f} STRONG_MED={med(strong):.3f} WEAK_AVG={avg(weak):.3f} WEAK_MED={med(weak):.3f}')
    tests=[
      ('RET3>=0.5',lambda x:x['ret3']>=0.5),
      ('RET3>=1.0',lambda x:x['ret3']>=1.0),
      ('H3>0',lambda x:x['h3']>0),
      ('H3>10',lambda x:x['h3']>10),
      ('RET3>=0.5&H3>0',lambda x:x['ret3']>=0.5 and x['h3']>0),
      ('RET3>=1&H3>0',lambda x:x['ret3']>=1 and x['h3']>0),
      ('RET3>=0.5&H3>0&MAEproxy',lambda x:x['ret3']>=0.5 and x['h3']>0 and x['acc']>-0.2),
      ('RET3>=0.5&H3>0&R14>0',lambda x:x['ret3']>=0.5 and x['h3']>0 and x['r14']>0),
      ('RET3>=0.5&H3>0&CCI>0',lambda x:x['ret3']>=0.5 and x['h3']>0 and x['cci']>0),
    ]
    base5=len(strong)/len(obs)
    for name,fn in tests:
        a=[x for x in obs if fn(x)]
        if not a: print(name,'N=0'); continue
        s=sum(x['mfe']>=5 for x in a)/len(a); w=sum(x['mfe']<1 for x in a)/len(a)
        print(f'{name} N={len(a)} STR5={100*s:.1f}% LIFT5={s/base5:.2f}x WEAK1={100*w:.1f}% MFE_AVG={statistics.fmean(x["mfe"] for x in a):.2f}% MAE_AVG={statistics.fmean(x["mae"] for x in a):.2f}%')
    print('--- STRONG5 ---')
    for x in strong:
        print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% RET1={x['ret1']:.2f} RET3={x['ret3']:.2f} RET5={x['ret5']:.2f} ACC={x['acc']:.2f} H1={x['h1']:.2f} H3={x['h3']:.2f} R14={x['r14']:.2f} CCI={x['cci']:.2f} VR={x['vr']:.2f}")
    print('--- WEAK1 ---')
    for x in weak:
        print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% RET1={x['ret1']:.2f} RET3={x['ret3']:.2f} RET5={x['ret5']:.2f} ACC={x['acc']:.2f} H1={x['h1']:.2f} H3={x['h3']:.2f} R14={x['r14']:.2f} CCI={x['cci']:.2f} VR={x['vr']:.2f}")
    con.close()

if __name__=='__main__': main()
