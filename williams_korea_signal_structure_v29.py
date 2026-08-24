#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def ema(vals,p):
    if not vals:return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

def rsi(vals,p=2):
    n=len(vals); out=[None]*n
    if n<p+2:return out
    gs=[]; ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-(100/(1+ag/al))
    return out

def cci(highs,lows,closes,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

def first_raw(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2)
    for i in range(20,len(cur)-2):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            return i
    return None

def summarize(name,arr,base3,base5):
    if not arr:
        print(name,'N=0'); return
    s3=sum(x['mfe']>=3 for x in arr)/len(arr)*100
    s5=sum(x['mfe']>=5 for x in arr)/len(arr)*100
    print(f"{name} N={len(arr)} MFE_AVG={statistics.fmean(x['mfe'] for x in arr):.2f}% STR3={s3:.1f}% LIFT3={s3/base3:.2f}x STR5={s5:.1f}% LIFT5={s5/base5:.2f}x MAE_AVG={statistics.fmean(x['mae'] for x in arr):.2f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; cur=dm[d]; prev=dm[ds[di-1]]; i=first_raw(prev,cur)
            if i is None: continue
            closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; vols=[float(r[5]) for r in cur]
            r2=rsi(closes,2); r14=rsi(closes,14); cc=cci(highs,lows,closes,20)
            e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
            ei=min(i+1,len(cur)-1); entry=closes[ei]
            mfe=max(pct(entry,h) for h in highs[ei:]); mae=min(pct(entry,l) for l in lows[ei:])
            ret1=pct(closes[i-1],closes[i])
            ret3=pct(closes[i-3],closes[i])
            accel=ret1-(pct(closes[i-3],closes[i-2]) if i>=3 else 0)
            vnow=sum(vols[i-2:i+1]); vprev=sum(vols[i-5:i-2]); volr=vnow/vprev if vprev>0 else 999
            def slope(a,k):
                if i-k<0 or a[i] is None or a[i-k] is None:return 0.0
                return (a[i]-a[i-k])/k
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,ret1=ret1,ret3=ret3,accel=accel,volr=volr,
                            r2s3=slope(r2,3),r14s3=slope(r14,3),ccs3=slope(cc,3),hs1=slope(hist,1),hs3=slope(hist,3)))
    print('=== WILLIAMS KOREA SIGNAL STRUCTURE V29 ===')
    print('No open-price/CONSUMED filter. All features are known at Williams signal time.')
    if not obs:
        print('N=0'); return
    base3=sum(x['mfe']>=3 for x in obs)/len(obs)*100; base5=sum(x['mfe']>=5 for x in obs)/len(obs)*100
    summarize('BASE',obs,base3,base5)
    tests=[
      ('RET1>0',[x for x in obs if x['ret1']>0]),
      ('RET3>=0.5',[x for x in obs if x['ret3']>=0.5]),
      ('PRICE_ACCEL>0',[x for x in obs if x['accel']>0]),
      ('VOLR>=1.5',[x for x in obs if x['volr']>=1.5]),
      ('RSI2_S3>0',[x for x in obs if x['r2s3']>0]),
      ('RSI14_S3>0',[x for x in obs if x['r14s3']>0]),
      ('CCI_S3>0',[x for x in obs if x['ccs3']>0]),
      ('HIST_S1>0',[x for x in obs if x['hs1']>0]),
      ('HIST_S3>0',[x for x in obs if x['hs3']>0]),
      ('VOL+HIST',[x for x in obs if x['volr']>=1.5 and x['hs1']>0]),
      ('ACCEL+HIST',[x for x in obs if x['accel']>0 and x['hs1']>0]),
      ('VOL+CCI_S3',[x for x in obs if x['volr']>=1.5 and x['ccs3']>0]),
      ('VOL+HIST+CCI',[x for x in obs if x['volr']>=1.5 and x['hs1']>0 and x['ccs3']>0]),
    ]
    for name,arr in tests: summarize(name,arr,base3,base5)
    print('--- STRONG MFE>=5% ---')
    for x in obs:
        if x['mfe']>=5:
            print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% RET1={x['ret1']:.2f}% RET3={x['ret3']:.2f}% ACC={x['accel']:.2f} VOLR={x['volr']:.2f} R2S3={x['r2s3']:.2f} R14S3={x['r14s3']:.2f} CCIS3={x['ccs3']:.2f} H1={x['hs1']:.4f} H3={x['hs3']:.4f}")
    con.close()

if __name__=='__main__': main()
