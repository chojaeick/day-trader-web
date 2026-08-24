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

def run(rows,ei,mode):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    e5=ema(c,5); e10=ema(c,10); e20=ema(c,20)
    entry=c[ei]; peak=entry; weak=0; state='WATCH'; exit_i=len(rows)-1; reason='EOD'
    for i in range(ei+1,len(rows)):
        peak=max(peak,h[i]); ret=pct(entry,c[i]); runup=pct(entry,peak); dd=pct(peak,c[i])
        if runup>=3: state='RUNNER'
        elif runup>=1: state='HOLD'
        below = c[i] < e5[i] and e5[i] < e10[i]
        if state=='RUNNER': below = below and c[i] < e20[i]
        weak = weak+1 if below else max(0,weak-1)

        # compare risk logic
        if mode=='HARD12':
            hard=-1.2
        elif mode=='HARD20':
            hard=-2.0
        else:
            hard=None

        if hard is not None and ret<=hard and state=='WATCH':
            exit_i=i; reason='HARD_STOP'; break

        if state=='WATCH':
            if weak>=4 and ret<0:
                exit_i=i; reason='FAILED_MOMENTUM'; break
        elif state=='HOLD':
            if weak>=4 and dd<=-1.5:
                exit_i=i; reason='HOLD_LOSS'; break
        else:
            trail=2.0 if runup<5 else 3.0 if runup<10 else 4.0
            if weak>=5 and dd<=-trail:
                exit_i=i; reason='RUNNER_EXIT'; break

    mfe=max(pct(entry,x) for x in h[ei:])
    mae=min(pct(entry,x) for x in l[ei:])
    ret=pct(entry,c[exit_i])
    cap=ret/mfe*100 if mfe>0 else 0
    return ret,mfe,mae,exit_i-ei,reason,cap

def metrics(name,tr):
    vals=[x[2] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MFE_AVG={statistics.fmean(x[3] for x in tr):.2f}% MAE_AVG={statistics.fmean(x[4] for x in tr):.2f}% HOLD_AVG={statistics.fmean(x[5] for x in tr):.1f}m CAP_MED={statistics.median(x[7] for x in tr):.1f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    entries=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            c=[float(r[4]) for r in cur]
            ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
            r2=rsi(c,2); idx=None
            for i in range(3,len(cur)-25):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is not None: entries.append((d,s,cur,idx+1))
    print('=== WILLIAMS KOREA RUNNER RESCUE V35 ===')
    print('Compare early risk logic while preserving runner hold. No fixed holding time.')
    for mode in ('HARD12','HARD20','NO_HARD'):
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap=run(rows,ei,mode)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap))
        metrics(mode,tr)
        print('BIG_MFE>=5')
        for t in tr:
            if t[3]>=5:
                print(f"{t[0]} {t[1]} RET={t[2]:.2f}% MFE={t[3]:.2f}% MAE={t[4]:.2f}% HOLD={t[5]}m CAP={t[7]:.1f}% {t[6]}")
    con.close()
if __name__=='__main__': main()
