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

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='daytrader.db')
    ap.add_argument('--max-days',type=int,default=20)
    args=ap.parse_args()
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
            for i in range(15,len(cur)-10):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1
            r14=rsi(c,14); cc=cci(h,l,c,9)
            e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
            mfe=max(pct(c[ei],x) for x in h[ei:]); mae=min(pct(c[ei],x) for x in l[ei:])
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

            # Distinguish impulse quality from exhaustion/recoil risk.
            sync3 = r14s3>0 and ccis3>0 and macs3>0 and sigs3>0
            impulse = r3>=1.0 or r1>=0.7
            extreme = r14v>=78 and cciv>=180
            recoil1 = (ccis1<0) or (hists1<0) or (macs1<0 and sigs1<0)
            weak_hist = hists3<=0
            low_participation = vr<0.8
            mac_above = mac[i]>sig[i]

            risk=sum([
                recoil1,
                extreme and r14s1<=0,
                extreme and ccis1<=0,
                weak_hist,
                low_participation and not impulse
            ])

            if sync3 and impulse and mac_above and risk==0:
                grade='A'
            elif sync3 and mac_above and risk<=1:
                grade='B'
            elif impulse and hists3>0 and risk<=1:
                grade='B'
            elif risk>=2:
                grade='D'
            else:
                grade='C'

            label='BIG5' if mfe>=5 else 'STRONG3' if mfe>=3 else 'WEAK1' if mfe<1 else 'MID'
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,label=label,grade=grade,risk=risk,
                r14=r14v,cci=cciv,r1=r1,r3=r3,r14s1=r14s1,r14s3=r14s3,ccis1=ccis1,ccis3=ccis3,
                macs1=macs1,macs3=macs3,sigs1=sigs1,sigs3=sigs3,hists1=hists1,hists3=hists3,vr=vr))

    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; oos=set(dates[cut:])
    print('=== WILLIAMS KOREA ENTRY REGIME REFINE V55 ===')
    print('Entry-time only. Grade A/B/C/D using synchronization + impulse + failure-risk, no future info.')
    print('OOS_DATES',','.join(sorted(oos)))
    for scope,name in [(obs,'ALL'),([x for x in obs if x['d'] in oos],'OOS')]:
        print('---',name,'GRADES ---')
        for g in ('A','B','C','D'):
            a=[x for x in scope if x['grade']==g]
            if not a: continue
            print(f"{g} N={len(a)} STR3={100*sum(x['mfe']>=3 for x in a)/len(a):.1f}% BIG5={100*sum(x['mfe']>=5 for x in a)/len(a):.1f}% WEAK1={100*sum(x['mfe']<1 for x in a)/len(a):.1f}% MFE_AVG={statistics.fmean(x['mfe'] for x in a):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in a):.2f}%")
        ab=[x for x in scope if x['grade'] in ('A','B')]
        if ab:
            print(f"A+B N={len(ab)} STR3={100*sum(x['mfe']>=3 for x in ab)/len(ab):.1f}% BIG5={100*sum(x['mfe']>=5 for x in ab)/len(ab):.1f}% WEAK1={100*sum(x['mfe']<1 for x in ab)/len(ab):.1f}% MFE_AVG={statistics.fmean(x['mfe'] for x in ab):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in ab):.2f}%")
    print('--- OOS OBS ---')
    for x in obs:
        if x['d'] in oos:
            print(f"{x['d']} {x['s']} {x['label']} GRADE={x['grade']} RISK={x['risk']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% R1={x['r1']:.2f}% R3={x['r3']:.2f}% R14={x['r14']:.1f} CCI={x['cci']:.1f} R14S1={x['r14s1']:.2f} CCIS1={x['ccis1']:.1f} H1={x['hists1']:.2f} H3={x['hists3']:.2f} VR={x['vr']:.2f}")
    con.close()

if __name__=='__main__': main()
