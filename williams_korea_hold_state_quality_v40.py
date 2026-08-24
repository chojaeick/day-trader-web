#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

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

def ema(vals,p):
    if not vals:return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
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
            c=[float(r[4]) for r in cur]; h=[float(r[2]) for r in cur]; l=[float(r[3]) for r in cur]; v=[float(r[5]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-25):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1; entry=c[ei]
            e5=ema(c,5); e10=ema(c,10); e20=ema(c,20)
            mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:])
            first_hold=None
            first_runner=None
            for i in range(ei+1,len(cur)):
                runup=max(pct(entry,x) for x in h[ei:i+1])
                r3=pct(c[max(ei,i-3)],c[i]) if i-ei>=3 else 0
                vnow=sum(v[max(ei,i-2):i+1]); vprev=sum(v[max(ei,i-5):max(ei,i-2)])
                vr=vnow/vprev if vprev>0 else 1.0
                strong=(1 if r3>=0.3 else 0)+(1 if c[i]>e5[i]>e10[i] else 0)+(1 if vr>=0.8 else 0)
                if first_hold is None and (strong>=2 or runup>=1):
                    first_hold=(i,pct(entry,c[i]),r3,vr,c[i]>e5[i]>e10[i])
                if first_runner is None and runup>=3:
                    first_runner=(i,pct(entry,c[i]))
                    break
            if first_hold is None:
                hold_min=None; hold_ret=None; hr3=None; hvr=None; hema=False
            else:
                hold_min=first_hold[0]-ei; hold_ret=first_hold[1]; hr3=first_hold[2]; hvr=first_hold[3]; hema=first_hold[4]
            runner_min=(first_runner[0]-ei) if first_runner else None
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,hold_min=hold_min,hold_ret=hold_ret,hr3=hr3,hvr=hvr,hema=hema,runner_min=runner_min))

    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; oos=set(dates[cut:])
    o=[x for x in obs if x['d'] in oos]
    print('=== WILLIAMS KOREA HOLD STATE QUALITY V40 ===')
    print('Diagnostic only. No retuning. Focus: whether HOLD promotion is too permissive.')
    print('OOS_DATES',','.join(sorted(oos)))
    for name,arr in [
        ('ALL',o),
        ('MFE<1',[x for x in o if x['mfe']<1]),
        ('MFE1_3',[x for x in o if 1<=x['mfe']<3]),
        ('MFE3_5',[x for x in o if 3<=x['mfe']<5]),
        ('MFE>=5',[x for x in o if x['mfe']>=5]),
    ]:
        if not arr: continue
        hm=[x['hold_min'] for x in arr if x['hold_min'] is not None]
        hr=[x['hold_ret'] for x in arr if x['hold_ret'] is not None]
        print(f"{name} N={len(arr)} HOLD_PROMOTED={sum(x['hold_min'] is not None for x in arr)} HOLD_MIN_AVG={(statistics.fmean(hm) if hm else 0):.1f} HOLD_RET_AVG={(statistics.fmean(hr) if hr else 0):.2f}% RUNNER={sum(x['runner_min'] is not None for x in arr)}")
    print('--- OOS HOLD OBS ---')
    for x in o:
        print(f"{x['d']} {x['s']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% HOLD_MIN={x['hold_min']} HOLD_RET={x['hold_ret']} R3={x['hr3']} VR={x['hvr']} EMA={x['hema']} RUNNER_MIN={x['runner_min']}")
    con.close()

if __name__=='__main__': main()
