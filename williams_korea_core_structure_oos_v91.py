#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
from collections import defaultdict
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

def metrics(rs):
    if not rs:return 'N=0'
    gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=gp/gl if gl else float('inf')
    return f"N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={statistics.fmean(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(rows)>=40: out[d]=rows
    return out

def run_struct0(rows,ei):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    entry=c[ei]; support=None; last_confirm=-1; updates=0
    # seed from most recent causally confirmed swing low before/at entry
    for i in range(4,ei+1):
        j=i-2
        if j>=2 and j+2<len(rows) and l[j] <= min(l[k] for k in range(j-2,j+3)):
            support=l[j]; last_confirm=j
    exit_i=len(rows)-1; reason='EOD_STRUCTURE_HELD'
    for i in range(ei+1,len(rows)):
        j=i-2
        if j>last_confirm and j>=2 and j+2<len(rows) and l[j] <= min(l[k] for k in range(j-2,j+3)):
            if support is None or l[j]>support:
                support=l[j]; updates+=1
            last_confirm=j
        if support is not None and c[i] < support:
            exit_i=i; reason='SUPPORT_BREAK_EXIT'; break
    ret=pct(entry,c[exit_i]); mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:])
    return ret,mfe,mae,exit_i-ei,reason,updates

ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); a=ap.parse_args()
con=sqlite3.connect(a.db)
syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
entries=[]
for s in syms:
    dm=load_days(con,s,a.max_days); ds=sorted(dm)
    for di in range(1,len(ds)):
        d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]; cc=[float(r[4]) for r in cur]
        ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
        r2=rsi(cc,2); idx=None
        for i in range(15,len(cur)-10):
            if cc[i-1] <= trig < cc[i] and r2[i] is not None and r2[i]>50:
                idx=i; break
        if idx is not None and idx+1<len(cur): entries.append((d,s,cur,idx+1))

dates=sorted(set(d for d,_,_,_ in entries)); cut=len(dates)//2; oos=set(dates[cut:])
tr=[]
for d,s,rows,ei in entries:
    ret,mfe,mae,hold,reason,updates=run_struct0(rows,ei)
    tr.append(dict(d=d,s=s,ret=ret,mfe=mfe,mae=mae,hold=hold,reason=reason,updates=updates))
oo=[x for x in tr if x['d'] in oos]
print('=== WILLIAMS KOREA CORE STRUCTURE OOS ROBUST V91 ===')
print('Freeze V89/V90 STRUCT0 exactly. No threshold retuning. Audit chronological OOS, symbol/day balance, and leave-one-out.')
print('OOS_DATES',','.join(sorted(oos)))
print('OOS_ALL',metrics([x['ret'] for x in oo]))
by=defaultdict(list)
for x in oo: by[x['s']].append(x['ret'])
if by:
    avgs=[statistics.fmean(v) for v in by.values()]; wins=[sum(z>0 for z in v)/len(v)*100 for v in by.values()]
    print(f"SYMBOL_BAL GROUPS={len(by)} AVG_OF_AVG={statistics.fmean(avgs):.3f}% AVG_WIN={statistics.fmean(wins):.1f}%")
print('--- OOS SYMBOL DETAIL ---')
for s,rs in sorted(by.items(),key=lambda kv:statistics.fmean(kv[1]),reverse=True): print(s,metrics(rs))
print('--- LEAVE ONE SYMBOL OUT ---')
for s in sorted(by): print('EX_'+s,metrics([x['ret'] for x in oo if x['s']!=s]))
byd=defaultdict(list)
for x in oo: byd[x['d']].append(x['ret'])
print('--- OOS DAY DETAIL ---')
for d,rs in sorted(byd.items()): print(d,metrics(rs))
if byd:
    davg=[statistics.fmean(v) for v in byd.values()]; dwin=[sum(z>0 for z in v)/len(v)*100 for v in byd.values()]
    print(f"DAY_BAL GROUPS={len(byd)} AVG_OF_AVG={statistics.fmean(davg):.3f}% AVG_WIN={statistics.fmean(dwin):.1f}%")
print('--- BIG5 OOS ---')
big=[x for x in oo if x['mfe']>=5]
print('N=',len(big))
for x in big:
    cap=x['ret']/x['mfe']*100 if x['mfe']>0 else 0
    print(x['d'],x['s'],f"RET={x['ret']:.2f}% MFE={x['mfe']:.2f}% CAP={cap:.1f}% HOLD={x['hold']}m {x['reason']}")
con.close()
