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

def safe_slope(arr,i,k=3):
    if i-k<0 or arr[i] is None or arr[i-k] is None:return 0.0
    return arr[i]-arr[i-k]

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
            for i in range(15,len(cur)-10):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1
            r14=rsi(c,14); cc=cci(h,l,c,9)
            e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
            mfe=max(pct(c[ei],x) for x in h[ei:]); mae=min(pct(c[ei],x) for x in l[ei:])
            i=idx
            vol3=sum(v[max(0,i-2):i+1]); pvol3=sum(v[max(0,i-5):max(0,i-2)]); vr=vol3/pvol3 if pvol3>0 else 1.0
            r14v=r14[i] or 50; cciv=cc[i] or 0
            feat=dict(
                r14=r14v, r14s1=safe_slope(r14,i,1), r14s3=safe_slope(r14,i,3),
                cci=cciv, ccis1=safe_slope(cc,i,1), ccis3=safe_slope(cc,i,3),
                mac=mac[i], macs1=safe_slope(mac,i,1), macs3=safe_slope(mac,i,3),
                sig=sig[i], sigs1=safe_slope(sig,i,1), sigs3=safe_slope(sig,i,3),
                hist=hist[i], hists1=safe_slope(hist,i,1), hists3=safe_slope(hist,i,3),
                mac_above=mac[i]>sig[i], vr=vr,
                ret1=pct(c[i-1],c[i]), ret3=pct(c[max(0,i-3)],c[i])
            )
            # regime-like entry classes, no future info
            trend_sync = feat['r14s3']>0 and feat['ccis3']>0 and feat['macs3']>0 and feat['sigs3']>0
            impulse = feat['ret3']>=1.0 or (feat['ret1']>=0.5 and vr>=1.5)
            mac_confirm = feat['mac_above'] and feat['hists3']>0
            overheat = feat['r14']>=80 and feat['cci']>=200
            recoil_risk = feat['r14s1']<0 or feat['ccis1']<0 or feat['hists1']<0
            if trend_sync and mac_confirm and impulse and not recoil_risk:
                regime='A_SYNC_IMPULSE'
            elif trend_sync and mac_confirm and not recoil_risk:
                regime='B_SYNC'
            elif impulse and not recoil_risk:
                regime='C_IMPULSE'
            elif recoil_risk:
                regime='D_RECOIL_RISK'
            else:
                regime='E_MIXED'
            label='BIG5' if mfe>=5 else 'STRONG3' if mfe>=3 else 'WEAK1' if mfe<1 else 'MID'
            obs.append(dict(d=d,s=s,mfe=mfe,mae=mae,label=label,regime=regime,overheat=overheat,**feat))
    dates=sorted(set(x['d'] for x in obs)); cut=len(dates)//2; oos=set(dates[cut:])
    print('=== WILLIAMS KOREA ENTRY REGIME FEATURES V54 ===')
    print('Entry-time only. Simple score failed; test synchronized trend/impulse/recoil regimes.')
    print('OOS_DATES',','.join(sorted(oos)))
    for scope,name in [(obs,'ALL'),([x for x in obs if x['d'] in oos],'OOS')]:
        print('---',name,'REGIMES ---')
        for rg in ('A_SYNC_IMPULSE','B_SYNC','C_IMPULSE','D_RECOIL_RISK','E_MIXED'):
            a=[x for x in scope if x['regime']==rg]
            if not a: continue
            print(f"{rg} N={len(a)} STR3={100*sum(x['mfe']>=3 for x in a)/len(a):.1f}% BIG5={100*sum(x['mfe']>=5 for x in a)/len(a):.1f}% WEAK1={100*sum(x['mfe']<1 for x in a)/len(a):.1f}% MFE_AVG={statistics.fmean(x['mfe'] for x in a):.2f}% MAE_AVG={statistics.fmean(x['mae'] for x in a):.2f}%")
    print('--- OOS OBS ---')
    for x in obs:
        if x['d'] in oos:
            print(f"{x['d']} {x['s']} {x['label']} {x['regime']} MFE={x['mfe']:.2f}% MAE={x['mae']:.2f}% RET1={x['ret1']:.2f}% RET3={x['ret3']:.2f}% R14S1={x['r14s1']:.2f} R14S3={x['r14s3']:.2f} CCIS1={x['ccis1']:.1f} CCIS3={x['ccis3']:.1f} MACS3={x['macs3']:.2f} SIGS3={x['sigs3']:.2f} HISTS1={x['hists1']:.2f} HISTS3={x['hists3']:.2f} VR={x['vr']:.2f}")
    con.close()

if __name__=='__main__': main()
