#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
from collections import Counter

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
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def confirmed_pivot_low(rows,j):
    if j<2 or j+2>=len(rows): return False
    x=float(rows[j][3])
    return x<=min(float(rows[k][3]) for k in range(j-2,j+3))

def initial_support(rows,ei):
    # Latest 5-bar pivot low already causally confirmed by entry time.
    last=None
    for j in range(2,max(2,ei-1)):
        if j+2<=ei and confirmed_pivot_low(rows,j): last=float(rows[j][3])
    if last is not None:return last
    lo=max(0,ei-10)
    return min(float(rows[k][3]) for k in range(lo,ei+1))

def run(rows,ei,tol=0.0):
    entry=float(rows[ei][4]); support=initial_support(rows,ei); peak=entry
    exit_i=len(rows)-1; reason='EOD_STRUCTURE_HELD'; support_updates=0
    for i in range(ei+1,len(rows)):
        peak=max(peak,float(rows[i][2]))
        # At bar i, pivot j=i-2 has just become causally confirmed.
        j=i-2
        if j>=ei and confirmed_pivot_low(rows,j):
            pl=float(rows[j][3])
            if pl>support:
                support=pl; support_updates+=1
        if float(rows[i][4]) < support*(1-tol/100):
            exit_i=i; reason='SUPPORT_BREAK_EXIT'; break
    highs=[float(r[2]) for r in rows[ei:]]; lows=[float(r[3]) for r in rows[ei:]]
    mfe=max(pct(entry,x) for x in highs); mae=min(pct(entry,x) for x in lows)
    ret=pct(entry,float(rows[exit_i][4])); hold=exit_i-ei; cap=ret/mfe*100 if mfe>0 else 0.0
    return ret,mfe,mae,hold,reason,cap,support_updates

def eod_run(rows,ei):
    entry=float(rows[ei][4]); ret=pct(entry,float(rows[-1][4]))
    highs=[float(r[2]) for r in rows[ei:]]; lows=[float(r[3]) for r in rows[ei:]]
    mfe=max(pct(entry,x) for x in highs); mae=min(pct(entry,x) for x in lows)
    cap=ret/mfe*100 if mfe>0 else 0.0
    return ret,mfe,mae,len(rows)-1-ei,'EOD',cap,0

def metrics(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x[2] for x in tr]; gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0)
    pf=gp/gl if gl else float('inf')
    print(f"{name} N={len(tr)} WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% AVG_RET={statistics.fmean(vals):.3f}% PF={pf:.3f} MED={statistics.median(vals):.3f}% HOLD={statistics.fmean(x[5] for x in tr):.1f}m")
    big=[x for x in tr if x[3]>=5]
    if big:
        print(f"  BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")
    print('  REASONS',dict(Counter(x[6] for x in tr)))

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
            if idx is not None and idx+1<len(cur): entries.append((d,s,cur,idx+1))
    dates=sorted(set(d for d,_,_,_ in entries)); cut=len(dates)//2; isd=set(dates[:cut]); oos=set(dates[cut:])
    print('=== WILLIAMS KOREA CORE STRUCTURE STATE V89 ===')
    print('Williams next-bar entry frozen. HOLD while confirmed higher-low support survives; EXIT on causal close-break. No reentry, no indicator exit, no retuning.')
    print('IS_DATES',','.join(sorted(isd))); print('OOS_DATES',','.join(sorted(oos)))
    modes=[('EOD_BASE',None),('STRUCT0',0.0),('STRUCT05',0.5)]
    for name,tol in modes:
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap,upd = eod_run(rows,ei) if tol is None else run(rows,ei,tol)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap,upd))
        print('\n---',name,'ALL ---'); metrics(name+'_ALL',tr)
        ii=[x for x in tr if x[0] in isd]; oo=[x for x in tr if x[0] in oos]
        print('---',name,'IS ---'); metrics(name+'_IS',ii)
        print('---',name,'OOS ---'); metrics(name+'_OOS',oo)
    print('\n--- OOS STRUCT0 TRADES ---')
    for d,s,rows,ei in entries:
        if d not in oos: continue
        ret,mfe,mae,hold,reason,cap,upd=run(rows,ei,0.0)
        print(f"{d} {s} RET={ret:.2f}% MFE={mfe:.2f}% MAE={mae:.2f}% HOLD={hold}m CAP={cap:.1f}% UPDATES={upd} {reason}")
    con.close()

if __name__=='__main__': main()
