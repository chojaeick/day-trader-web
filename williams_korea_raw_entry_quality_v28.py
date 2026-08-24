#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
from datetime import datetime, timezone, timedelta
KR_RE=re.compile(r'^\d{6}$')
KST=timezone(timedelta(hours=9))

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

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

def raw_indices(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-2):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append(i)
    return out,trig,r2

def metrics(arr):
    if not arr:return 'N=0'
    mfe=[x['mfe'] for x in arr]; mae=[x['mae'] for x in arr]; eod=[x['eod'] for x in arr]
    strong=sum(x>=3 for x in mfe)
    return f"N={len(arr)} MFE_AVG={statistics.fmean(mfe):.2f}% MFE>=3={100*strong/len(arr):.1f}% MAE_AVG={statistics.fmean(mae):.2f}% EOD_AVG={statistics.fmean(eod):.2f}%"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    obs=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; cur=dm[d]; prev=dm[ds[di-1]]
            raws,trig,r2=raw_indices(prev,cur)
            if not raws: continue
            i=raws[0]
            closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]
            entry_i=min(i+1,len(cur)-1); entry=closes[entry_i]; op=float(cur[0][1])
            consumed=pct(op,entry); mfe=max(pct(entry,h) for h in highs[entry_i:]); mae=min(pct(entry,l) for l in lows[entry_i:]); eod=pct(entry,closes[-1])
            # causal features at signal time only
            ret3=pct(closes[max(0,i-3)],closes[i]) if i>=3 else 0
            ret5=pct(closes[max(0,i-5)],closes[i]) if i>=5 else 0
            v_now=sum(float(cur[j][5]) for j in range(max(0,i-2),i+1))
            v_prev=sum(float(cur[j][5]) for j in range(max(0,i-5),max(0,i-2)))
            vol_ratio=(v_now/v_prev) if v_prev>0 else 999
            obs.append(dict(d=d,s=s,consumed=consumed,mfe=mfe,mae=mae,eod=eod,ret3=ret3,ret5=ret5,vol=vol_ratio,rsi2=r2[i] or 0))
    print('=== WILLIAMS KOREA RAW ENTRY QUALITY V28 ===')
    print('All filters use only data known at the Williams signal time.')
    print('BASE',metrics(obs))
    tests=[
      ('CONSUMED<=3',[x for x in obs if x['consumed']<=3]),
      ('CONSUMED<=4',[x for x in obs if x['consumed']<=4]),
      ('CONSUMED<=5',[x for x in obs if x['consumed']<=5]),
      ('RET3>=0.3',[x for x in obs if x['ret3']>=0.3]),
      ('RET5>=0.5',[x for x in obs if x['ret5']>=0.5]),
      ('VOL3/PREV3>=1.5',[x for x in obs if x['vol']>=1.5]),
      ('EARLY3+RET3',[x for x in obs if x['consumed']<=3 and x['ret3']>=0.3]),
      ('EARLY4+RET3',[x for x in obs if x['consumed']<=4 and x['ret3']>=0.3]),
      ('EARLY4+VOL',[x for x in obs if x['consumed']<=4 and x['vol']>=1.5]),
    ]
    for name,arr in tests: print(name,metrics(arr))
    print('--- OBS ---')
    for x in obs:
        print(f"{x['d']} {x['s']} CONSUMED={x['consumed']:.2f}% RET3={x['ret3']:.2f}% RET5={x['ret5']:.2f}% VOLR={x['vol']:.2f} RSI2={x['rsi2']:.1f} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% EOD={x['eod']:.2f}%")
    con.close()

if __name__=='__main__': main()
