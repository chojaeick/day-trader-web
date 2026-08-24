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

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def classify(c,h,l,entry_i,i):
    entry=c[entry_i]
    age=i-entry_i
    def ret(k):
        j=min(entry_i+k,i)
        return pct(entry,c[j])
    def maxr(k):
        j=min(entry_i+k,i)
        return max(pct(entry,x) for x in h[entry_i:j+1])
    def minr(k):
        j=min(entry_i+k,i)
        return min(pct(entry,x) for x in l[entry_i:j+1])
    r5=ret(5) if age>=5 else pct(entry,c[i])
    r10=ret(10) if age>=10 else pct(entry,c[i])
    r15=ret(15) if age>=15 else pct(entry,c[i])
    r20=ret(20) if age>=20 else pct(entry,c[i])
    m10=maxr(10); m20=maxr(20); n10=minr(10)

    if age>=5 and (m10>=2.0 or r5>=1.0 or (age>=10 and r10>=1.0)):
        return 'FAST_RUNNER'
    if age>=15 and ((m20>=2.0 and (r15>=0.5 or r20>=0.8)) or (maxr(15)>=1.5 and r15>0)):
        return 'LATENT_RUNNER'
    if age>=10 and m20<0.8 and (r10<0 or (age>=20 and r20<0)) and n10<=-0.5:
        return 'WEAK'
    return 'UNCERTAIN'

def run(rows,ei,mode):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    e5=ema(c,5); e10=ema(c,10); e20=ema(c,20)
    entry=c[ei]; peak=entry; weakbars=0; exit_i=len(rows)-1; reason='EOD'; state='UNCERTAIN'
    for i in range(ei+1,len(rows)):
        peak=max(peak,h[i]); runup=pct(entry,peak); ret=pct(entry,c[i]); dd=pct(peak,c[i]); age=i-ei
        state=classify(c,h,l,ei,i)
        below_fast=c[i]<e5[i] and e5[i]<e10[i]
        below_struct=c[i]<e20[i]
        weakbars=weakbars+1 if below_fast else max(0,weakbars-1)

        if runup>=3 or state in ('FAST_RUNNER','LATENT_RUNNER'):
            # runner-style protection
            trail=1.3 if runup<5 else 1.8 if runup<10 else 2.5
            if runup>=3:
                floor=None
                if runup>=10: floor=7.0
                elif runup>=7: floor=4.8
                elif runup>=5: floor=3.2
                elif runup>=3: floor=1.6
                if floor is not None and ret<=floor and weakbars>=2:
                    exit_i=i; reason='PROFIT_FLOOR'; break
            if weakbars>=4 and below_struct and dd<=-trail:
                exit_i=i; reason='RUNNER_STRUCT_EXIT'; break
            continue

        if state=='WEAK':
            if mode=='PATH':
                if ret<=-0.45 or (weakbars>=2 and below_struct):
                    exit_i=i; reason='WEAK_PATH_EXIT'; break
            else: # PATH_SOFT
                if ret<=-0.65 or (weakbars>=3 and below_struct):
                    exit_i=i; reason='WEAK_PATH_EXIT'; break
            continue

        # UNCERTAIN: do not overreact; only exit on persistent structural failure
        if age>=12 and weakbars>=4 and below_struct and ret<0:
            exit_i=i; reason='UNCERTAIN_STRUCT_EXIT'; break

    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i])
    cap=ret/mfe*100 if mfe>0 else 0.0
    return ret,mfe,mae,exit_i-ei,reason,cap,state

def metrics(name,tr):
    vals=[x[2] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    eq=pk=mdd=0
    for x in vals:
        eq+=x; pk=max(pk,eq); mdd=min(mdd,eq-pk)
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MDD={mdd:.3f}% HOLD_AVG={statistics.fmean(x[5] for x in tr):.1f}m")
    for bucket,cond in [('MFE<1',lambda x:x[3]<1),('MFE1_3',lambda x:1<=x[3]<3),('MFE>=3',lambda x:x[3]>=3),('MFE>=5',lambda x:x[3]>=5)]:
        a=[x for x in tr if cond(x)]
        if a:
            print(f"  {bucket} N={len(a)} RET_AVG={statistics.fmean(x[2] for x in a):.2f}% CAP_AVG={statistics.fmean(x[7] for x in a):.1f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    entries=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]; c=[float(r[4]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-35):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is not None: entries.append((d,s,cur,idx+1))
    dates=sorted(set(d for d,_,_,_ in entries)); cut=len(dates)//2; oos=set(dates[cut:])
    print('=== WILLIAMS KOREA PATH STATE EXIT V47 ===')
    print('Uses V46 causal path states: WEAK / UNCERTAIN / FAST_RUNNER / LATENT_RUNNER. No fixed holding time.')
    print('OOS_DATES',','.join(sorted(oos)))
    for mode in ('PATH','PATH_SOFT'):
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap,state=run(rows,ei,mode)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap,state))
        print('\n---',mode,'ALL ---'); metrics(mode+'_ALL',tr)
        oo=[x for x in tr if x[0] in oos]
        print('---',mode,'OOS ---'); metrics(mode+'_OOS',oo)
        print('OOS TRADES')
        for x in oo:
            print(f"{x[0]} {x[1]} RET={x[2]:.2f}% MFE={x[3]:.2f}% MAE={x[4]:.2f}% HOLD={x[5]}m {x[6]} LASTSTATE={x[8]}")
    con.close()

if __name__=='__main__': main()
