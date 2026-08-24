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

def cci(h,l,c,p=9):
    out=[None]*len(c); tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    for i in range(p-1,len(c)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def slope(a,i,k):
    if i-k<0 or a[i] is None or a[i-k] is None:return 0.0
    return a[i]-a[i-k]

def grade_entry(c,h,l,v,idx):
    r14=rsi(c,14); cc=cci(h,l,c,9)
    e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
    i=idx
    r14v=r14[i] or 50; cciv=cc[i] or 0
    r1=pct(c[i-1],c[i]); r3=pct(c[max(0,i-3)],c[i])
    r14s1=slope(r14,i,1); r14s3=slope(r14,i,3)
    ccis1=slope(cc,i,1); ccis3=slope(cc,i,3)
    macs1=slope(mac,i,1); macs3=slope(mac,i,3)
    sigs1=slope(sig,i,1); sigs3=slope(sig,i,3)
    hists1=slope(hist,i,1); hists3=slope(hist,i,3)
    vol3=sum(v[max(0,i-2):i+1]); pvol3=sum(v[max(0,i-5):max(0,i-2)])
    vr=vol3/pvol3 if pvol3>0 else 1.0
    sync3=r14s3>0 and ccis3>0 and macs3>0 and sigs3>0
    impulse=r3>=1.0 or r1>=0.7
    extreme=r14v>=78 and cciv>=180
    recoil1=(ccis1<0) or (hists1<0) or (macs1<0 and sigs1<0)
    weak_hist=hists3<=0
    low_participation=vr<0.8
    mac_above=mac[i]>sig[i]
    risk=sum([recoil1, extreme and r14s1<=0, extreme and ccis1<=0, weak_hist, low_participation and not impulse])
    if sync3 and impulse and mac_above and risk==0: grade='A'
    elif (sync3 and mac_above and risk<=1) or (impulse and hists3>0 and risk<=1): grade='B'
    elif risk>=2: grade='D'
    else: grade='C'
    return grade,risk

def run_exit(rows,ei):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    r14=rsi(c,14); cc=cci(h,l,c,9)
    e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
    entry=c[ei]; peak=entry; exit_i=len(rows)-1; reason='EOD'; weak_streak=0; strong_streak=0
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
                fire=(rsi75 and cci100) or (dead and rdn and cdn)
                if fire and ret>0:
                    exit_i=i; reason='RUNNER_EXIT'; break
            continue
        if rebound: continue
        if weak_streak>=2 and ret<=0:
            exit_i=i; reason='WEAK_EXIT'; break
    return pct(entry,c[exit_i]), max(pct(entry,x) for x in h[ei:]), min(pct(entry,x) for x in l[ei:]), exit_i-ei, reason

def metrics(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x['ret'] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    print(f"{name} N={len(tr)} WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% AVG={statistics.fmean(vals):.3f}% PF={pf:.3f} MFE_AVG={statistics.fmean(x['mfe'] for x in tr):.2f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    trades=[]
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
            g,risk=grade_entry(c,h,l,v,idx)
            ret,mfe,mae,hold,reason=run_exit(cur,idx+1)
            trades.append(dict(d=d,s=s,grade=g,risk=risk,ret=ret,mfe=mfe,mae=mae,hold=hold,reason=reason))
    dates=sorted(set(x['d'] for x in trades)); cut=len(dates)//2; oos=set(dates[cut:])
    print('=== WILLIAMS KOREA ENTRY GRADE GATE V56 ===')
    print('V55 grades + V52 runner-first exit. Test whether entry gating improves OOS win-rate without killing runners.')
    print('OOS_DATES',','.join(sorted(oos)))
    for scope,name in [(trades,'ALL'),([x for x in trades if x['d'] in oos],'OOS')]:
        print('---',name,'---')
        metrics('ALL_TRADES',scope)
        metrics('A_ONLY',[x for x in scope if x['grade']=='A'])
        metrics('A_OR_D',[x for x in scope if x['grade'] in ('A','D')])
        metrics('NOT_C',[x for x in scope if x['grade']!='C'])
        metrics('RISK0',[x for x in scope if x['risk']==0])
        print('OBS')
        for x in scope:
            print(f"{x['d']} {x['s']} G={x['grade']} RISK={x['risk']} RET={x['ret']:.2f}% MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% {x['reason']}")
    con.close()

if __name__=='__main__': main()
