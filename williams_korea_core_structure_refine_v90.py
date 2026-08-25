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

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def confirmed_swings(rows):
    out=[]
    for i in range(4,len(rows)):
        j=i-2
        if rows[j][3] <= min(rows[k][3] for k in range(j-2,j+3)):
            out.append((i,j,float(rows[j][3])))
    return out

def run(rows,ei,mode):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    entry=c[ei]; swings=confirmed_swings(rows)
    support=None; updates=0; peak=entry; exit_i=len(rows)-1; reason='EOD_STRUCTURE_HELD'
    # initialize from latest confirmed swing low known by entry
    for confirm,j,low in swings:
        if confirm<=ei: support=low
        else: break
    if support is None:
        support=min(l[max(0,ei-10):ei+1])
    for i in range(ei+1,len(rows)):
        peak=max(peak,h[i])
        # only promote support after a newly confirmed swing low is higher than old support
        for confirm,j,low in swings:
            if confirm==i and low>support:
                support=low; updates+=1
        ret=pct(entry,c[i]); runup=pct(entry,peak)
        if mode=='STRUCT0':
            broke=c[i] < support
        elif mode=='STRUCT03':
            broke=c[i] < support*0.997
        elif mode=='STRUCT0_RUNNER_LOCK':
            # same hard structure break, but once +3% runner exists, require one extra close below support
            if runup>=3 and c[i] < support:
                if i+1 < len(rows) and c[i+1] < support:
                    exit_i=i+1; reason='RUNNER_SUPPORT_2CLOSE'; break
                broke=False
            else:
                broke=c[i] < support
        elif mode=='STRUCT0_PROFIT_FLOOR':
            broke=c[i] < support
            # protect some captured profit only after a real runner; never tighten before +3
            if runup>=3 and ret>0 and c[i] < entry*(1+0.005):
                exit_i=i; reason='RUNNER_PROFIT_FLOOR'; break
        else:
            broke=False
        if broke:
            exit_i=i; reason='SUPPORT_BREAK_EXIT'; break
    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i]); cap=ret/mfe*100 if mfe>0 else 0
    return ret,mfe,mae,exit_i-ei,reason,cap,updates

def metrics(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x[2] for x in tr]; gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else float('inf')
    print(f"{name} N={len(tr)} WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% AVG_RET={statistics.fmean(vals):.3f}% PF={pf:.3f} MED={statistics.median(vals):.3f}% HOLD={statistics.fmean(x[5] for x in tr):.1f}m")
    big=[x for x in tr if x[3]>=5]
    if big:
        print(f"BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")
    from collections import Counter
    print('REASONS',dict(Counter(x[6] for x in tr)))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    entries=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]
            c=[float(r[4]) for r in cur]; ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl); r2=rsi(c,2)
            idx=None
            for i in range(15,len(cur)-10):
                if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
                    idx=i; break
            if idx is not None and idx+1<len(cur): entries.append((d,s,cur,idx+1))
    dates=sorted(set(d for d,_,_,_ in entries)); cut=max(1,len(dates)//2); isd=set(dates[:cut]); oos=set(dates[cut:])
    print('=== WILLIAMS KOREA CORE STRUCTURE REFINE V90 ===')
    print('Freeze Williams entry. Compare minimal structure variants only; no reentry and no indicator exits.')
    print('IS_DATES',','.join(sorted(isd))); print('OOS_DATES',','.join(sorted(oos)))
    for mode in ('STRUCT0','STRUCT03','STRUCT0_RUNNER_LOCK','STRUCT0_PROFIT_FLOOR'):
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap,updates=run(rows,ei,mode)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap,updates))
        print('\n---',mode,'ALL ---'); metrics(mode+'_ALL',tr)
        print('---',mode,'IS ---'); metrics(mode+'_IS',[x for x in tr if x[0] in isd])
        print('---',mode,'OOS ---'); metrics(mode+'_OOS',[x for x in tr if x[0] in oos])
    con.close()

if __name__=='__main__': main()
