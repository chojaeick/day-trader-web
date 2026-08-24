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

def metrics(name,arr,base3,base5):
    if not arr:
        print(name,'N=0'); return
    mfe=[x['mfe'] for x in arr]; mae=[x['mae'] for x in arr]
    s3=sum(x>=3 for x in mfe)/len(arr); s5=sum(x>=5 for x in mfe)/len(arr)
    print(f"{name} N={len(arr)} MFE_AVG={statistics.fmean(mfe):.2f}% STR3={s3*100:.1f}% LIFT3={s3/base3:.2f}x STR5={s5*100:.1f}% LIFT5={s5/base5:.2f}x MAE_AVG={statistics.fmean(mae):.2f}%")

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
            ret3=pct(closes[idx-3],closes[idx]); ret1=pct(closes[idx-1],closes[idx])
            acc=ret1-(pct(closes[idx-3],closes[idx-1])/2)
            h3=hist[idx]-hist[idx-3]
            r14s3=(r14[idx]-r14[idx-3]) if r14[idx] is not None and r14[idx-3] is not None else 0
            ccis3=(cc[idx]-cc[idx-3]) if cc[idx] is not None and cc[idx-3] is not None else 0
            vnow=sum(vols[idx-2:idx+1]); vprev=sum(vols[idx-5:idx-2]); vr=vnow/vprev if vprev>0 else 999
            score=sum([
                ret3>=0.5,
                h3>0,
                acc>0,
                r14s3>0,
                ccis3>0,
                vr>=1.5,
            ])
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,ret3=ret3,h3=h3,acc=acc,r14=r14s3,cci=ccis3,vr=vr,score=score))
    print('=== WILLIAMS KOREA STRONG MOMENTUM COMPOSITE V30 ===')
    print('No open-price filter. Features are causal at Williams signal time.')
    base3=sum(x['mfe']>=3 for x in obs)/len(obs); base5=sum(x['mfe']>=5 for x in obs)/len(obs)
    metrics('BASE',obs,base3,base5)
    for k in range(2,7):
        metrics(f'SCORE>={k}',[x for x in obs if x['score']>=k],base3,base5)
    metrics('RET3+HIST',[x for x in obs if x['ret3']>=0.5 and x['h3']>0],base3,base5)
    metrics('RET3+ACCEL',[x for x in obs if x['ret3']>=0.5 and x['acc']>0],base3,base5)
    metrics('RET3+HIST+ACCEL',[x for x in obs if x['ret3']>=0.5 and x['h3']>0 and x['acc']>0],base3,base5)
    metrics('RET3+HIST+R14',[x for x in obs if x['ret3']>=0.5 and x['h3']>0 and x['r14']>0],base3,base5)
    print('--- SCORE>=4 OBS ---')
    for x in obs:
        if x['score']>=4:
            print(f"{x['d']} {x['s']} SCORE={x['score']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% RET3={x['ret3']:.2f}% H3={x['h3']:.2f} ACC={x['acc']:.2f} R14S3={x['r14']:.2f} CCIS3={x['cci']:.2f} VOLR={x['vr']:.2f}")
    con.close()

if __name__=='__main__': main()
