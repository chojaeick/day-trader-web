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

def first_confirm_after(evs,bt,w=30):
    cand=[dt for dt,rank in evs if rank<=20 and 0 < (dt-bt).total_seconds()/60 <= w]
    return min(cand) if cand else None

def first_bar_after(rows,dt):
    for i,r in enumerate(rows):
        bt=pb(r[6])
        if bt and bt>dt: return i
    return None

def fmt_time(x):
    return x.strftime('%H:%M') if x else 'NA'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='daytrader.db')
    ap.add_argument('--max-days',type=int,default=20)
    args=ap.parse_args()
    con=sqlite3.connect(args.db); evmap=load_events(con)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    out=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; rows=dm[d]; prev=dm[ds[di-1]]; evs=evmap.get((s,d),[])
            closes=[float(r[4]) for r in rows]; highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]
            raws=raw_indices(prev,rows)
            if not raws: continue
            chosen=None
            for wi in raws:
                bt=pb(rows[wi][6])
                if not bt: continue
                ev=first_confirm_after(evs,bt,30)
                if not ev: continue
                ei=first_bar_after(rows,ev)
                if ei is None: continue
                cand=(ev,wi,ei)
                if chosen is None or ev<chosen[0]: chosen=cand
            if not chosen: continue
            ev,wi,ei=chosen
            entry=closes[ei]
            peak_i=max(range(ei,len(rows)), key=lambda j: highs[j])
            trough_i=min(range(ei,len(rows)), key=lambda j: lows[j])
            mfe=pct(entry,highs[peak_i]); mae=pct(entry,lows[trough_i]); eod=pct(entry,closes[-1])
            day_o=float(rows[0][1]); day_h=max(highs); day_o2h=pct(day_o,day_h)
            consumed=pct(day_o,entry)
            post_share=(mfe/day_o2h*100) if day_o2h>0 else 0.0
            raw_price=closes[wi]
            raw_to_entry=pct(raw_price,entry)
            lag=(pb(rows[ei][6])-pb(rows[wi][6])).total_seconds()/60 if pb(rows[ei][6]) and pb(rows[wi][6]) else None
            out.append((d,s,day_o2h,consumed,raw_to_entry,lag,mfe,mae,eod,fmt_time(pb(rows[wi][6])),fmt_time(ev),fmt_time(pb(rows[ei][6])),fmt_time(pb(rows[peak_i][6])),post_share))
    print('=== WILLIAMS KOREA ENTRY TIMING MFE AUDIT V26 ===')
    print('Question: are we entering too late, or exiting badly?')
    print('DAY_O2H=full-day open->high; CONSUMED=open->entry move; MFE=entry->future high')
    for t in out:
        print(f'{t[0]} {t[1]} DAY_O2H={t[2]:.2f}% CONSUMED={t[3]:.2f}% RAW2ENTRY={t[4]:.2f}% LAG={t[5]:.1f}m MFE={t[6]:.2f}% MAE={t[7]:.2f}% EOD={t[8]:.2f}% RAW={t[9]} FINDER={t[10]} ENTRY={t[11]} PEAK={t[12]} POST_SHARE={t[13]:.1f}%')
    if out:
        print('\n=== SUMMARY ===')
        print(f'N={len(out)} DAY_O2H_AVG={statistics.fmean(t[2] for t in out):.2f}%')
        print(f'CONSUMED_AVG={statistics.fmean(t[3] for t in out):.2f}% RAW2ENTRY_AVG={statistics.fmean(t[4] for t in out):.2f}%')
        print(f'MFE_AVG={statistics.fmean(t[6] for t in out):.2f}% MAE_AVG={statistics.fmean(t[7] for t in out):.2f}% EOD_AVG={statistics.fmean(t[8] for t in out):.2f}%')
        print(f'POST_SHARE_MEDIAN={statistics.median(t[13] for t in out):.1f}%')
    con.close()

if __name__=='__main__': main()
