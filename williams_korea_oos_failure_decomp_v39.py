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
    state_max='WATCH'
    for i in range(ei+1,len(rows)):
        peak=max(peak,h[i]); ret=pct(entry,c[i]); runup=pct(entry,peak); dd=pct(peak,c[i])
        r3=pct(c[max(ei,i-3)],c[i]) if i-ei>=3 else 0
        vnow=sum(v[max(ei,i-2):i+1]); vprev=sum(v[max(ei,i-5):max(ei,i-2)])
        vr=vnow/vprev if vprev>0 else 1.0
        strong=(1 if r3>=0.3 else 0)+(1 if c[i]>e5[i]>e10[i] else 0)+(1 if vr>=0.8 else 0)
        if runup>=3:
            state='RUNNER'; state_max='RUNNER'
        elif strong>=2 or runup>=1:
            state='HOLD'
            if state_max=='WATCH': state_max='HOLD'

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

    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i])
    return ret,mfe,mae,exit_i-ei,reason,state_max

def summarize(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x[2] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MFE_AVG={statistics.fmean(x[3] for x in tr):.2f}% MAE_AVG={statistics.fmean(x[4] for x in tr):.2f}%")
    for bucket,cond in [
        ('MFE<1',lambda x:x[3]<1),
        ('MFE1_3',lambda x:1<=x[3]<3),
        ('MFE3_5',lambda x:3<=x[3]<5),
        ('MFE>=5',lambda x:x[3]>=5),
    ]:
        a=[x for x in tr if cond(x)]
        if a:
            print(f"  {bucket} N={len(a)} RET_AVG={statistics.fmean(x[2] for x in a):.2f}% MAE_AVG={statistics.fmean(x[4] for x in a):.2f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    tr=[]
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
            ret,mfe,mae,hold,reason,state=run(cur,idx+1)
            tr.append((d,s,ret,mfe,mae,hold,reason,state))
    dates=sorted(set(x[0] for x in tr)); cut=len(dates)//2; oos=set(dates[cut:])
    o=[x for x in tr if x[0] in oos]
    print('=== WILLIAMS KOREA OOS FAILURE DECOMP V39 ===')
    print('Frozen V37 LOCK_TIGHT logic. Diagnostic only: no retuning.')
    print('OOS_DATES',','.join(sorted(oos)))
    summarize('OOS',o)
    print('--- BY EXIT REASON ---')
    for reason in sorted(set(x[6] for x in o)):
        a=[x for x in o if x[6]==reason]
        print(f"{reason} N={len(a)} RET_AVG={statistics.fmean(x[2] for x in a):.2f}% MFE_AVG={statistics.fmean(x[3] for x in a):.2f}% MAE_AVG={statistics.fmean(x[4] for x in a):.2f}%")
    print('--- BY MAX STATE ---')
    for st in ('WATCH','HOLD','RUNNER'):
        a=[x for x in o if x[7]==st]
        if a:
            print(f"{st} N={len(a)} RET_AVG={statistics.fmean(x[2] for x in a):.2f}% MFE_AVG={statistics.fmean(x[3] for x in a):.2f}% MAE_AVG={statistics.fmean(x[4] for x in a):.2f}%")
    print('--- OOS LOSERS WITH MFE ---')
    for x in o:
        if x[2] < 0:
            print(f"{x[0]} {x[1]} RET={x[2]:.2f}% MFE={x[3]:.2f}% MAE={x[4]:.2f}% HOLD={x[5]}m {x[6]} MAXSTATE={x[7]}")
    con.close()

if __name__=='__main__': main()
