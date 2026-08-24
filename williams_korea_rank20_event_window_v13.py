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
    out[p]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def parse_event_ts(s):
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except Exception: return None

def parse_bar_ts(s):
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except Exception: return None

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=20: out[d]=rows
    return out

def raw(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-25):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes,cur[i][6]))
    return out

def load_events(con):
    rows=con.execute("""select ts,symbol,rank_to from v4_signal_events
        where market='KOREA' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by ts""").fetchall()
    out={}
    for ts,s,rank in rows:
        dt=parse_event_ts(ts)
        if not dt: continue
        out.setdefault((str(s),dt.strftime('%Y%m%d')),[]).append((dt,int(rank or 999999)))
    return out

def matched(events,bt,window,mode):
    for dt,rank in events:
        if rank>20: continue
        delta=(bt-dt).total_seconds()/60
        if mode=='BEFORE' and 0 <= delta <= window: return True
        if mode=='AFTER' and -window <= delta < 0: return True
        if mode=='AROUND' and abs(delta) <= window: return True
    return False

def metrics(xs):
    if not xs: return {'n':0,'avg':0,'win':0,'pf':0,'mdd':0}
    avg=statistics.fmean(xs); win=100*sum(x>0 for x in xs)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(xs),'avg':avg,'win':win,'pf':pf,'mdd':mdd}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db); evmap=load_events(con)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    rules=[(m,w) for m in ('BEFORE','AFTER','AROUND') for w in (5,15,30,60)]
    rec={r:[] for r in rules}; dates={r:set() for r in rules}
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; evs=evmap.get((s,d),[])
            for i,closes,bts in raw(dm[ds[di-1]],dm[d]):
                bt=parse_bar_ts(bts)
                if not bt: continue
                ret=pct(closes[i],closes[min(i+5,len(closes)-1)])
                for rule in rules:
                    if matched(evs,bt,rule[1],rule[0]):
                        rec[rule].append((d,ret)); dates[rule].add(d)
    print('=== WILLIAMS KOREA RANK20 EVENT WINDOW V13 ===')
    for rule in rules:
        dts=sorted(dates[rule]); split=len(dts)//2; isd=set(dts[:split]); oosd=set(dts[split:])
        am=metrics([x for _,x in rec[rule]]); im=metrics([x for d,x in rec[rule] if d in isd]); om=metrics([x for d,x in rec[rule] if d in oosd])
        print(f"{rule[0]} {rule[1]}m DATES={dts} | ALL N={am['n']} AVG={am['avg']:.4f}% PF={am['pf']:.3f} | IS N={im['n']} AVG={im['avg']:.4f}% PF={im['pf']:.3f} | OOS N={om['n']} AVG={om['avg']:.4f}% WIN={om['win']:.2f}% PF={om['pf']:.3f} MDD={om['mdd']:.4f}%")
    con.close()
if __name__=='__main__': main()
