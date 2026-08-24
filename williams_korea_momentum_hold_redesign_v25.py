#!/usr/bin/env python3
import argparse, sqlite3, re, statistics, math
from datetime import datetime, timezone, timedelta

KR_RE=re.compile(r'^\d{6}$')
KST=timezone(timedelta(hours=9))

def ema(vals,p):
    if not vals: return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

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
    for i in range(2,len(cur)-10):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i] > 50:
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

def atr14(highs,lows,closes):
    tr=[]
    for i in range(len(closes)):
        if i==0: tr.append(highs[i]-lows[i])
        else: tr.append(max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])))
    out=[None]*len(closes)
    if len(tr)<14: return out
    a=sum(tr[:14])/14; out[13]=a
    for i in range(14,len(tr)):
        a=(a*13+tr[i])/14; out[i]=a
    return out

def exit_trade(rows,entry_i):
    closes=[float(r[4]) for r in rows]; highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]
    vols=[float(r[5]) for r in rows]
    e5=ema(closes,5); e10=ema(closes,10); e20=ema(closes,20)
    a14=atr14(highs,lows,closes)
    entry=closes[entry_i]
    peak=entry; peak_i=entry_i; mfe=0.0; mae=0.0
    weak_count=0
    hard_stop=-1.2
    reason='EOD'
    exit_i=len(rows)-1
    for i in range(entry_i+1,len(rows)):
        peak=max(peak,highs[i])
        if highs[i] >= peak: peak_i=i
        runup=pct(entry,peak)
        dd_from_peak=pct(peak,closes[i])
        ret=pct(entry,closes[i])
        mfe=max(mfe,pct(entry,highs[i]))
        mae=min(mae,pct(entry,lows[i]))
        atrp=(a14[i]/closes[i]*100) if a14[i] else 0.0

        # widening trail as profit expands
        if runup < 0.8:
            trail=max(0.55,1.1*atrp)
        elif runup < 2.0:
            trail=max(0.80,1.5*atrp)
        elif runup < 4.0:
            trail=max(1.15,1.9*atrp)
        elif runup < 7.0:
            trail=max(1.60,2.4*atrp)
        else:
            trail=max(2.20,3.0*atrp)

        # only count real momentum loss, not a single wiggle
        weak = closes[i] < e5[i] and e5[i] < e10[i]
        if runup >= 2.0:
            weak = weak and closes[i] < e20[i]
        if weak: weak_count += 1
        else: weak_count = max(0,weak_count-1)

        if ret <= hard_stop:
            exit_i=i; reason='HARD_STOP'; break

        # don't trail before trade has had a chance to expand
        armed = runup >= 0.8
        if armed and dd_from_peak <= -trail:
            exit_i=i; reason='WIDE_TRAIL'; break

        # as profit grows, require more confirmation before momentum-loss exit
        need=3 if runup < 2 else 4 if runup < 5 else 5
        if weak_count >= need:
            exit_i=i; reason='MOMENTUM_LOSS'; break

    ret=pct(entry,closes[exit_i])
    capture=(ret/mfe*100) if mfe>0 else 0.0
    return ret,mfe,mae,exit_i-entry_i,reason,pct(entry,peak),capture,peak_i-entry_i

def metrics(xs):
    if not xs: return (0,0,0,0,0)
    avg=statistics.fmean(xs); win=100*sum(x>0 for x in xs)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return len(xs),avg,win,pf,mdd

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db); evmap=load_events(con)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    trades=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; rows=dm[d]; evs=evmap.get((s,d),[])
            chosen=None
            for wi in raw_indices(dm[ds[di-1]],rows):
                bt=pb(rows[wi][6])
                if not bt: continue
                ev=first_confirm_after(evs,bt,30)
                if not ev: continue
                ei=first_bar_after(rows,ev)
                if ei is None: continue
                if chosen is None or ev < chosen[0]: chosen=(ev,ei)
            if chosen:
                ret,mfe,mae,hold,reason,peak,capture,peakmin=exit_trade(rows,chosen[1])
                trades.append((d,s,ret,mfe,mae,hold,reason,peak,capture,peakmin))
    vals=[t[2] for t in trades]
    n,avg,win,pf,mdd=metrics(vals)
    print('=== WILLIAMS KOREA MOMENTUM HOLD REDESIGN V25 ===')
    print('ENTRY=Williams -> Finder rank<=20 within 30m -> next 1m bar; one trade/symbol/day')
    print('EXIT=no fixed horizon; widening ATR trail + multi-bar momentum-loss confirmation')
    print(f'ALL N={n} AVG={avg:.4f}% WIN={win:.2f}% PF={pf:.3f} MDD={mdd:.4f}%')
    if trades:
        print(f'MFE_AVG={statistics.fmean(t[3] for t in trades):.4f}% MAE_AVG={statistics.fmean(t[4] for t in trades):.4f}% HOLD_AVG={statistics.fmean(t[5] for t in trades):.1f}m CAPTURE_MEDIAN={statistics.median(t[8] for t in trades):.2f}%')
    print('TRADES')
    for t in trades:
        print(f'{t[0]} {t[1]} RET={t[2]:.3f}% MFE={t[3]:.3f}% MAE={t[4]:.3f}% HOLD={t[5]}m PEAK={t[7]:.3f}% PEAK_AT={t[9]}m CAPTURE={t[8]:.1f}% {t[6]}')
    con.close()

if __name__=='__main__': main()
