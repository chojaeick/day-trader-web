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

def run(rows,ei,mode):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    r14=rsi(c,14); cc=cci(h,l,c,9)
    e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
    entry=c[ei]; peak=entry; exit_i=len(rows)-1; reason='EOD'
    state='WATCH'; weak_streak=0; strong_streak=0; ever_strong=False

    for i in range(max(ei+1,15),len(rows)):
        peak=max(peak,h[i]); ret=pct(entry,c[i]); runup=pct(entry,peak)
        r_now=r14[i] if r14[i] is not None else 50; r_prev=r14[i-1] if r14[i-1] is not None else r_now
        c_now=cc[i] if cc[i] is not None else 0; c_prev=cc[i-1] if cc[i-1] is not None else c_now
        r_up=r_now>r_prev; r_down=r_now<r_prev
        c_up=c_now>c_prev; c_down=c_now<c_prev
        mac_up=mac[i]>mac[i-1]; mac_down=mac[i]<mac[i-1]
        sig_up=sig[i]>sig[i-1]; sig_down=sig[i]<sig[i-1]
        hist_up=hist[i]>hist[i-1]; hist_down=hist[i]<hist[i-1]
        golden=mac[i]>sig[i] and mac[i-1]<=sig[i-1]
        dead=mac[i]<sig[i] and mac[i-1]>=sig[i-1]
        rsi75_exit=r_prev>=75 and r_now<75
        cci100_exit=c_prev>=100 and c_now<100
        rebound=(r_prev<=30 and r_up) or golden or (hist_up and hist[i-1]<=hist[i-2])

        strong_axes=sum([
            r_now>=60 and r_up,
            c_now>=100 and c_up,
            mac[i]>sig[i] and mac_up,
            sig_up and hist_up
        ])
        weak_axes=sum([
            r_down,
            c_down,
            mac_down and sig_down,
            dead or hist_down
        ])

        strong_streak = strong_streak+1 if strong_axes>=2 else max(0,strong_streak-1)
        weak_streak = weak_streak+1 if weak_axes>=2 else max(0,weak_streak-1)

        # Runner/strong evidence is evaluated BEFORE any weak exit.
        if runup>=3 or strong_streak>=2:
            state='RUNNER' if runup>=3 or strong_axes>=3 else 'STRONG'
            ever_strong=True
        elif rebound:
            state='REARM'
        else:
            state='WATCH'

        if state=='RUNNER':
            if mode=='RUNNER_FIRST':
                fire=(rsi75_exit and cci100_exit) or (dead and r_down and c_down)
            else:
                fire=sum([rsi75_exit,cci100_exit,dead,mac_down and sig_down])>=2
            if fire and ret>0:
                exit_i=i; reason='RUNNER_EXIT'; break
            continue

        if state=='STRONG':
            # Strong but not yet runner: do not exit on first wiggle.
            if weak_streak>=3 and ret<0 and not rebound:
                exit_i=i; reason='STRONG_FAIL'; break
            continue

        if state=='REARM':
            continue

        # WATCH: only weak momentum that persists may exit.
        if ever_strong:
            req=3
        else:
            req=2 if mode=='RUNNER_FIRST' else 1
        if weak_streak>=req and ret<=0:
            exit_i=i; reason='WATCH_WEAK_EXIT'; break

    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i]); cap=ret/mfe*100 if mfe>0 else 0
    return ret,mfe,mae,exit_i-ei,reason,cap,state

def metrics(name,tr):
    vals=[x[2] for x in tr]
    gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    eq=pk=mdd=0
    for x in vals:
        eq+=x; pk=max(pk,eq); mdd=min(mdd,eq-pk)
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MDD={mdd:.3f}% HOLD={statistics.fmean(x[5] for x in tr):.1f}m")
    big=[x for x in tr if x[3]>=5]
    if big:
        print(f"  BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")

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
            for i in range(15,len(cur)-10):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is not None: entries.append((d,s,cur,idx+1))
    dates=sorted(set(d for d,_,_,_ in entries)); cut=len(dates)//2; oos=set(dates[cut:])
    print('=== WILLIAMS KOREA RUNNER-FIRST STATE V52 ===')
    print('Strong/runner detection has priority over weak exits. Weak exits require persistence; REARM blocks exit.')
    print('OOS_DATES',','.join(sorted(oos)))
    for mode in ('RUNNER_FIRST','AND2_BASE'):
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap,state=run(rows,ei,mode)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap,state))
        print('\n---',mode,'ALL ---'); metrics(mode+'_ALL',tr)
        oo=[x for x in tr if x[0] in oos]
        print('---',mode,'OOS ---'); metrics(mode+'_OOS',oo)
        print('OOS TRADES')
        for x in oo:
            print(f"{x[0]} {x[1]} RET={x[2]:.2f}% MFE={x[3]:.2f}% MAE={x[4]:.2f}% HOLD={x[5]}m {x[6]} LAST={x[8]}")
    con.close()

if __name__=='__main__': main()
