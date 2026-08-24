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

def pe(s):
    try:
        dt=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST)
    except Exception: return None

def pb(s):
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
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-65):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append(i)
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

def first_confirm_after(evs,bt,w):
    cand=[]
    for dt,rank in evs:
        delta=(dt-bt).total_seconds()/60.0
        if rank<=20 and 0 < delta <= w:
            cand.append(dt)
    return min(cand) if cand else None

def first_bar_after(rows,dt):
    for idx,r in enumerate(rows):
        bt=pb(r[6])
        if bt and bt>dt: return idx
    return None

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
    configs=[(15,5),(30,5),(60,5),(60,3)]
    rec={c:[] for c in configs}; dates={c:set() for c in configs}
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; rows=dm[d]; closes=[float(r[4]) for r in rows]; evs=evmap.get((s,d),[])
            for wi in raw(dm[ds[di-1]],rows):
                bt=pb(rows[wi][6])
                if not bt: continue
                for cfg in configs:
                    w,h=cfg; ev=first_confirm_after(evs,bt,w)
                    if not ev: continue
                    ei=first_bar_after(rows,ev)
                    if ei is None: continue
                    j=min(ei+h,len(closes)-1)
                    rec[cfg].append((d,pct(closes[ei],closes[j]))); dates[cfg].add(d)
    costs=(0.00,0.05,0.10,0.15,0.20,0.25)
    print('=== WILLIAMS KOREA CAUSAL COST STRESS V17 ===')
    print('COST=round-trip fee+slippage deduction in percentage points')
    for cfg in configs:
        dts=sorted(dates[cfg]); split=len(dts)//2; oosd=set(dts[split:])
        base=[x for d,x in rec[cfg] if d in oosd]
        print(f'--- W{cfg[0]} H{cfg[1]} OOS_DATES={sorted(oosd)} BASE_N={len(base)} ---')
        for c in costs:
            m=metrics([x-c for x in base])
            print(f'COST {c:.2f}% N={m["n"]} AVG={m["avg"]:.4f}% WIN={m["win"]:.2f}% PF={m["pf"]:.3f} MDD={m["mdd"]:.4f}%')
    con.close()
if __name__=='__main__': main()
