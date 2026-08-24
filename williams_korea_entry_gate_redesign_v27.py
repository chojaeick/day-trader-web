#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
from datetime import datetime, timezone, timedelta
KR_RE=re.compile(r'^\d{6}$')
KST=timezone(timedelta(hours=9))

def rsi(vals,p=2):
    n=len(vals); out=[None]*n
    if n<p+2: return out
    gs=[]; ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def pe(s):
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except: return None

def pb(s):
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except: return None

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

def load_events(con):
    rows=con.execute("""select ts,symbol,rank_to from v4_signal_events
      where market='KOREA' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by ts""").fetchall()
    out={}
    for ts,s,rank in rows:
        dt=pe(ts)
        if not dt: continue
        out.setdefault((str(s),dt.strftime('%Y%m%d')),[]).append((dt,int(rank or 999999)))
    return out

def raw_indices(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-2):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append(i)
    return out

def first_bar_after(rows,dt):
    for i,r in enumerate(rows):
        bt=pb(r[6])
        if bt and bt>dt: return i
    return None

def mfe_from(rows,ei):
    closes=[float(r[4]) for r in rows]; highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]
    entry=closes[ei]
    mfe=max(pct(entry,h) for h in highs[ei:])
    mae=min(pct(entry,l) for l in lows[ei:])
    eod=pct(entry,closes[-1])
    return mfe,mae,eod

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db); evmap=load_events(con)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    rowsout=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; cur=dm[d]; prev=dm[ds[di-1]]; raws=raw_indices(prev,cur)
            if not raws: continue
            wi=raws[0]
            raw_t=pb(cur[wi][6]); raw_entry_i=min(wi+1,len(cur)-1)
            evs=evmap.get((s,d),[])
            ranks=[(dt,rank) for dt,rank in evs if dt>=raw_t]
            first10=min([x for x in ranks if x[1]<=10],default=None,key=lambda x:x[0])
            first20=min([x for x in ranks if x[1]<=20],default=None,key=lambda x:x[0])
            modes=[('RAW_NEXT',raw_t,raw_entry_i)]
            for name,x in [('RANK10',first10),('RANK20',first20)]:
                if x and (x[0]-raw_t).total_seconds()/60 <= 30:
                    ei=first_bar_after(cur,x[0])
                    if ei is not None: modes.append((name,x[0],ei))
            full_o=float(cur[0][1]); full_h=max(float(r[2]) for r in cur); o2h=pct(full_o,full_h)
            for name,ct,ei in modes:
                entry=float(cur[ei][4]); consumed=pct(full_o,entry); mfe,mae,eod=mfe_from(cur,ei)
                lag=(pb(cur[ei][6])-raw_t).total_seconds()/60 if raw_t and pb(cur[ei][6]) else 0
                rowsout.append((d,s,name,lag,o2h,consumed,mfe,mae,eod))
    print('=== WILLIAMS KOREA ENTRY GATE REDESIGN V27 ===')
    print('Compare causal entry timing: RAW next-bar vs Finder rank<=10/20 confirmations within 30m')
    for name in ('RAW_NEXT','RANK10','RANK20'):
        arr=[x for x in rowsout if x[2]==name]
        if not arr: continue
        print(f'--- {name} N={len(arr)} ---')
        print(f'LAG_AVG={statistics.fmean(x[3] for x in arr):.1f}m DAY_O2H_AVG={statistics.fmean(x[4] for x in arr):.2f}% CONSUMED_AVG={statistics.fmean(x[5] for x in arr):.2f}% MFE_AVG={statistics.fmean(x[6] for x in arr):.2f}% MAE_AVG={statistics.fmean(x[7] for x in arr):.2f}% EOD_AVG={statistics.fmean(x[8] for x in arr):.2f}%')
        for x in arr:
            print(f'{x[0]} {x[1]} LAG={x[3]:.1f}m O2H={x[4]:.2f}% CONSUMED={x[5]:.2f}% MFE={x[6]:.2f}% MAE={x[7]:.2f}% EOD={x[8]:.2f}%')
    con.close()

if __name__=='__main__': main()
