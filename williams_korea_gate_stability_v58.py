#!/usr/bin/env python3
import argparse, sqlite3, re, statistics, itertools
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

def cci(h,l,c,p=9):
    out=[None]*len(c); tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    for i in range(p-1,len(c)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0
def slope(a,i,k):
    if i-k<0 or a[i] is None or a[i-k] is None:return 0.0
    return a[i]-a[i-k]

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def entry_feats(c,h,l,v,idx):
    r14=rsi(c,14); cc=cci(h,l,c,9)
    e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
    i=idx
    vol3=sum(v[max(0,i-2):i+1]); pvol3=sum(v[max(0,i-5):max(0,i-2)])
    return dict(
        r1=pct(c[i-1],c[i]), r3=pct(c[max(0,i-3)],c[i]),
        r14s1=slope(r14,i,1), r14s3=slope(r14,i,3),
        ccis1=slope(cc,i,1), ccis3=slope(cc,i,3),
        mac_above=mac[i]>sig[i],
        h1=slope(hist,i,1), h3=slope(hist,i,3),
        sigs3=slope(sig,i,3),
        vr=(vol3/pvol3 if pvol3>0 else 1.0)
    )

def run_exit(rows,ei):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    r14=rsi(c,14); cc=cci(h,l,c,9)
    e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
    entry=c[ei]; peak=entry; exit_i=len(rows)-1; weak_streak=0; strong_streak=0
    for i in range(max(ei+1,15),len(rows)):
        peak=max(peak,h[i]); ret=pct(entry,c[i]); runup=pct(entry,peak)
        rv=r14[i] if r14[i] is not None else 50; rp=r14[i-1] if r14[i-1] is not None else rv
        cv=cc[i] if cc[i] is not None else 0; cp=cc[i-1] if cc[i-1] is not None else cv
        rup=rv>rp; rdn=rv<rp; cup=cv>cp; cdn=cv<cp
        mup=mac[i]>mac[i-1]; mdn=mac[i]<mac[i-1]; sup=sig[i]>sig[i-1]; sdn=sig[i]<sig[i-1]
        hup=hist[i]>hist[i-1]; hdn=hist[i]<hist[i-1]
        dead=mac[i]<sig[i] and mac[i-1]>=sig[i-1]
        golden=mac[i]>sig[i] and mac[i-1]<=sig[i-1]
        rebound=(rp<=30 and rup) or golden or (hup and hist[i-1]<=hist[i-2])
        strong=sum([rv>=60 and rup, cv>=100 and cup, mac[i]>sig[i] and mup, sup and hup])
        weak=sum([rdn,cdn,mdn and sdn,dead or hdn])
        strong_streak=strong_streak+1 if strong>=2 else max(0,strong_streak-1)
        weak_streak=weak_streak+1 if weak>=2 else max(0,weak_streak-1)
        if runup>=3 or strong_streak>=2:
            if runup>=3:
                rsi75=rp>=75 and rv<75; cci100=cp>=100 and cv<100
                if ((rsi75 and cci100) or (dead and rdn and cdn)) and ret>0:
                    exit_i=i; break
            continue
        if rebound: continue
        if weak_streak>=2 and ret<=0:
            exit_i=i; break
    return pct(entry,c[exit_i])

def met(a):
    if not a:return None
    vals=[x['ret'] for x in a]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0)
    pf=gp/gl if gl else 999
    return len(a),100*sum(x>0 for x in vals)/len(vals),statistics.fmean(vals),pf

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    rows=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            c=[float(r[4]) for r in cur]; h=[float(r[2]) for r in cur]; l=[float(r[3]) for r in cur]; v=[float(r[5]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(15,len(cur)-10):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            f=entry_feats(c,h,l,v,idx)
            rows.append(dict(d=d,s=s,ret=run_exit(cur,idx+1),**f))
    dates=sorted(set(x['d'] for x in rows)); cut=len(dates)//2; isd=set(dates[:cut]); oosd=set(dates[cut:])
    tests={
      'MAC_ABOVE':lambda x:x['mac_above'],
      'H3>0':lambda x:x['h3']>0,
      'H1>0':lambda x:x['h1']>0,
      'CCI_S1>0':lambda x:x['ccis1']>0,
      'CCI_S3>0':lambda x:x['ccis3']>0,
      'R14_S1>0':lambda x:x['r14s1']>0,
      'R14_S3>0':lambda x:x['r14s3']>0,
      'SIG_S3>0':lambda x:x['sigs3']>0,
      'RET3>=0.5':lambda x:x['r3']>=0.5,
      'RET3>=1.0':lambda x:x['r3']>=1.0,
      'VR>=1.0':lambda x:x['vr']>=1.0,
      'VR>=1.5':lambda x:x['vr']>=1.5,
    }
    print('=== WILLIAMS KOREA GATE STABILITY V58 ===')
    print('Reject gates that only look good OOS. Rank by worst-split win rate, then worst-split PF.')
    print('IS_DATES',','.join(sorted(isd))); print('OOS_DATES',','.join(sorted(oosd)))
    cand=[]; names=list(tests)
    for k in (1,2,3):
        for combo in itertools.combinations(names,k):
            fn=lambda x,combo=combo: all(tests[n](x) for n in combo)
            a=[x for x in rows if x['d'] in isd and fn(x)]
            b=[x for x in rows if x['d'] in oosd and fn(x)]
            if len(a)<4 or len(b)<4: continue
            ma,mb=met(a),met(b)
            worst_win=min(ma[1],mb[1]); worst_pf=min(ma[3],mb[3])
            gap=abs(ma[1]-mb[1])
            cand.append((worst_win,worst_pf,-gap,min(ma[0],mb[0]),combo,ma,mb))
    cand.sort(reverse=True)
    print('--- TOP STABLE GATES ---')
    for z in cand[:30]:
        combo='+'.join(z[4]); ma=z[5]; mb=z[6]
        print(f"{combo} WORST_WIN={z[0]:.1f}% WORST_PF={z[1]:.3f} | IS N={ma[0]} WIN={ma[1]:.1f}% AVG={ma[2]:.3f}% PF={ma[3]:.3f} | OOS N={mb[0]} WIN={mb[1]:.1f}% AVG={mb[2]:.3f}% PF={mb[3]:.3f}")
    con.close()

if __name__=='__main__': main()
