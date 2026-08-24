#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KR_RE=re.compile(r'^\d{6}$')
KST=ZoneInfo('Asia/Seoul')


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
    if not s: return None
    s=str(s).strip()
    dt=None
    try:
        dt=datetime.fromisoformat(s.replace('Z','+00:00'))
    except Exception:
        for fmt in ('%Y-%m-%d %H:%M:%S','%Y%m%d%H%M%S'):
            try:
                dt=datetime.strptime(s,fmt); break
            except Exception: pass
    if dt is None: return None
    # v4_signal_events are stored in UTC. Treat naive timestamps as UTC too.
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def parse_bar_ts(s):
    if not s: return None
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=20: out[d]=rows
    return out


def raw(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]
    r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-65):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes,highs,cur[i][6]))
    return out


def load_finder_events(con):
    rows=con.execute("select ts,symbol,event_type,state_to,power,rank_to from v4_signal_events where market='KOREA' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by ts").fetchall()
    out={}; bad=0
    for ts,sym,ev,st,power,rank in rows:
        dt=parse_event_ts(ts)
        if not dt:
            bad+=1; continue
        d=dt.strftime('%Y%m%d')
        out.setdefault((str(sym),d),[]).append((dt,ev,st,power,rank))
    return out,bad


def active_near(events,bar_ts,window_min):
    bt=parse_bar_ts(bar_ts)
    if bt is None or not events: return False
    for e in events:
        delta=abs((bt-e[0]).total_seconds())/60.0
        if delta<=window_min: return True
    return False


def metrics(xs):
    if not xs: return {'n':0,'avg':0,'win':0,'pf':0,'mdd':0}
    avg=statistics.fmean(xs); win=100*sum(x>0 for x in xs)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(xs),'avg':avg,'win':win,'pf':pf,'mdd':mdd}


def show(name,m):
    print(f"{name} N={m['n']} AVG={m['avg']:.4f}% WIN={m['win']:.2f}% PF={m['pf']:.3f} MDD={m['mdd']:.4f}%")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    finder,bad=load_finder_events(con)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    windows=(5,15,30,60)
    rec={w:[] for w in windows}; dates={w:set() for w in windows}; raw_n=0
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; evs=finder.get((s,d),[])
            for i,closes,highs,bts in raw(dm[ds[di-1]],dm[d]):
                raw_n+=1
                j=min(i+10,len(closes)-1)
                ret=pct(closes[i],closes[j])
                for w in windows:
                    if active_near(evs,bts,w):
                        rec[w].append((d,ret)); dates[w].add(d)
    print('=== WILLIAMS KOREA FINDER-TIME FILTER V10 ===')
    print('TIME_FIX=event UTC -> KST; bar timestamps normalized to KST')
    print('RAW_SIGNALS=',raw_n,'FINDER_KEYS=',len(finder),'BAD_EVENT_TS=',bad)
    for w in windows:
        dts=sorted(dates[w]); split=len(dts)//2; isd=set(dts[:split]); oosd=set(dts[split:])
        print(f'--- WINDOW {w}m DATES={dts} IS={len(isd)} OOS={len(oosd)} ---')
        show('ALL',metrics([x for _,x in rec[w]]))
        show('IS ',metrics([x for d,x in rec[w] if d in isd]))
        show('OOS',metrics([x for d,x in rec[w] if d in oosd]))
    con.close()

if __name__=='__main__': main()
