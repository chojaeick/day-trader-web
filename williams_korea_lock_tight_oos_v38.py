#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

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

def run(rows,ei):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]; v=[float(r[5]) for r in rows]
    e5=ema(c,5); e10=ema(c,10); e20=ema(c,20)
    entry=c[ei]; peak=entry; weak=0; state='WATCH'; exit_i=len(rows)-1; reason='EOD'
    for i in range(ei+1,len(rows)):
        peak=max(peak,h[i]); ret=pct(entry,c[i]); runup=pct(entry,peak); dd=pct(peak,c[i])
        r3=pct(c[max(ei,i-3)],c[i]) if i-ei>=3 else 0
        vnow=sum(v[max(ei,i-2):i+1]); vprev=sum(v[max(ei,i-5):max(ei,i-2)])
        vr=vnow/vprev if vprev>0 else 1.0
        strong=(1 if r3>=0.3 else 0)+(1 if c[i]>e5[i]>e10[i] else 0)+(1 if vr>=0.8 else 0)
        if runup>=3: state='RUNNER'
        elif strong>=2 or runup>=1: state='HOLD'
        below_fast=c[i]<e5[i] and e5[i]<e10[i]
        below_struct=c[i]<e20[i]
        weak=weak+1 if below_fast else max(0,weak-1)

        if state=='WATCH':
            if weak>=4 and below_struct and ret<0:
                exit_i=i; reason='STRUCT_FAIL'; break
        elif state=='HOLD':
            if weak>=4 and below_struct and dd<=-1.5:
                exit_i=i; reason='HOLD_STRUCT_FAIL'; break
        else:
            trail=1.3 if runup<5 else 1.8 if runup<10 else 2.5
            if weak>=4 and below_struct and dd<=-trail:
                exit_i=i; reason='RUNNER_STRUCT_EXIT'; break
            floor=None
            if runup>=10: floor=7.0
            elif runup>=7: floor=4.8
            elif runup>=5: floor=3.2
            elif runup>=3: floor=1.6
            if floor is not None and ret<=floor and weak>=2:
                exit_i=i; reason='PROFIT_FLOOR'; break

    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i]); cap=ret/mfe*100 if mfe>0 else 0
    return ret,mfe,mae,exit_i-ei,reason,cap

def metrics(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x[2] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    eq=pk=mdd=0
    for x in vals:
        eq+=x; pk=max(pk,eq); mdd=min(mdd,eq-pk)
    big=[x for x in tr if x[3]>=5]
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MDD={mdd:.3f}% HOLD_AVG={statistics.fmean(x[5] for x in tr):.1f}m")
    if big:
        print(f"{name}_BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% MFE_AVG={statistics.fmean(x[3] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    trades=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]; c=[float(r[4]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-25):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is None: continue
            ei=idx+1
            ret,mfe,mae,hold,reason,cap=run(cur,ei)
            trades.append((d,s,ret,mfe,mae,hold,reason,cap))
    dates=sorted(set(t[0] for t in trades))
    cut=len(dates)//2
    is_dates=set(dates[:cut]); oos_dates=set(dates[cut:])
    is_tr=[t for t in trades if t[0] in is_dates]
    oos_tr=[t for t in trades if t[0] in oos_dates]
    print('=== WILLIAMS KOREA LOCK_TIGHT OOS V38 ===')
    print('Rule frozen from V37 LOCK_TIGHT. Chronological split by date. No retuning in this script.')
    print('DATES',','.join(dates))
    print('IS_DATES',','.join(sorted(is_dates)))
    print('OOS_DATES',','.join(sorted(oos_dates)))
    metrics('ALL',trades); metrics('IS',is_tr); metrics('OOS',oos_tr)
    for cost in (0.10,0.20,0.25):
        adj=[]
        for t in oos_tr:
            q=list(t); q[2]=q[2]-cost; adj.append(tuple(q))
        metrics(f'OOS_COST{int(cost*100)}',adj)
    print('--- OOS TRADES ---')
    for t in oos_tr:
        print(f"{t[0]} {t[1]} RET={t[2]:.2f}% MFE={t[3]:.2f}% MAE={t[4]:.2f}% HOLD={t[5]}m CAP={t[7]:.1f}% {t[6]}")
    con.close()
if __name__=='__main__': main()
