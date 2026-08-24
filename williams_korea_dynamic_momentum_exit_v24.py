#!/usr/bin/env python3
import argparse, sqlite3, re, statistics, math
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

def ema(vals,p):
    out=[]; a=2/(p+1); x=None
    for v in vals:
        x=v if x is None else a*v+(1-a)*x
        out.append(x)
    return out

def cci(highs,lows,closes,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
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
        if len(rows)>=30: out[d]=rows
    return out

def raw(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-5):
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

def first_confirm_after(evs,bt,w=30):
    cand=[dt for dt,rank in evs if rank<=20 and 0 < (dt-bt).total_seconds()/60.0 <= w]
    return min(cand) if cand else None

def first_bar_after(rows,dt):
    for idx,r in enumerate(rows):
        bt=pb(r[6])
        if bt and bt>dt: return idx
    return None

def trail_width(peak_ret):
    if peak_ret < 0.5: return None
    if peak_ret < 1.0: return 0.45
    if peak_ret < 2.0: return 0.65
    if peak_ret < 3.0: return 0.90
    if peak_ret < 5.0: return 1.25
    if peak_ret < 8.0: return 1.75
    return 2.50

def simulate(rows,ei,variant):
    closes=[float(r[4]) for r in rows]; highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]
    e5=ema(closes,5); e12=ema(closes,12); e26=ema(closes,26)
    macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    cc=cci(highs,lows,closes,20)
    entry=closes[ei]; peak=entry; peak_ret=0.0; mfe=0.0; mae=0.0; weak_count=0; reason='EOD'; exit_i=len(rows)-1
    for i in range(ei+1,len(rows)):
        peak=max(peak,highs[i]); peak_ret=max(peak_ret,pct(entry,peak)); mfe=max(mfe,pct(entry,highs[i])); mae=min(mae,pct(entry,lows[i]))
        cur=pct(entry,closes[i]); tw=trail_width(peak_ret)
        # initial risk only before profit lock
        if peak_ret < 0.5 and cur <= -0.8:
            reason='HARD_STOP'; exit_i=i; break
        # adaptive trailing: wider as trend profit expands
        if tw is not None and pct(peak,closes[i]) <= -tw:
            reason='ADAPT_TRAIL'; exit_i=i; break
        if variant!='TRAIL_ONLY' and i>=2:
            cci_down = cc[i] is not None and cc[i-1] is not None and cc[i] < cc[i-1]
            hist_down = hist[i] < hist[i-1]
            price_weak = closes[i] < e5[i]
            weak = cci_down and hist_down and price_weak
            weak_count = weak_count+1 if weak else 0
            # do not let small wiggles shake out a strong runner
            need = 2 if peak_ret < 2.0 else (3 if peak_ret < 5.0 else 4)
            if weak_count >= need and peak_ret >= 0.5:
                reason='MOMENTUM_LOSS'; exit_i=i; break
    ret=pct(entry,closes[exit_i])
    return ret,mfe,mae,exit_i-ei,reason,peak_ret

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
    variants=('TRAIL_ONLY','TRAIL_MOMENTUM')
    trades={v:[] for v in variants}; seen_day=set()
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; rows=dm[d]; evs=evmap.get((s,d),[]); chosen=None
            for wi in raw(dm[ds[di-1]],rows):
                bt=pb(rows[wi][6])
                if not bt: continue
                ev=first_confirm_after(evs,bt,30)
                if not ev: continue
                ei=first_bar_after(rows,ev)
                if ei is None: continue
                cand=(ev,ei)
                if chosen is None or cand[0]<chosen[0]: chosen=cand
            if chosen is None: continue
            ei=chosen[1]
            for v in variants:
                ret,mfe,mae,hold,reason,peak_ret=simulate(rows,ei,v)
                trades[v].append((d,s,ret,mfe,mae,hold,reason,peak_ret))
    print('=== WILLIAMS KOREA DYNAMIC MOMENTUM EXIT V24 ===')
    print('ENTRY=Williams -> Finder rank<=20 within 30m -> next 1m bar; max one trade/symbol/day')
    print('EXIT=no fixed hold. Adaptive trailing widens as profit grows; optional confirmed momentum-loss exit.')
    for v in variants:
        arr=trades[v]; m=metrics([x[2] for x in arr]); m25=metrics([x[2]-0.25 for x in arr])
        print(f'--- {v} ---')
        print(f'ALL N={m["n"]} AVG={m["avg"]:.4f}% WIN={m["win"]:.2f}% PF={m["pf"]:.3f} MDD={m["mdd"]:.4f}%')
        print(f'COST25 N={m25["n"]} AVG={m25["avg"]:.4f}% WIN={m25["win"]:.2f}% PF={m25["pf"]:.3f} MDD={m25["mdd"]:.4f}%')
        if arr:
            print(f'MFE_AVG={statistics.fmean(x[3] for x in arr):.4f}% MAE_AVG={statistics.fmean(x[4] for x in arr):.4f}% HOLD_AVG={statistics.fmean(x[5] for x in arr):.1f}m')
            cap=[(x[2]/x[3]*100) for x in arr if x[3]>0]
            print(f'MFE_CAPTURE_AVG={statistics.fmean(cap):.2f}%')
        print('TRADES')
        for x in arr:
            print(x[0],x[1],f'RET={x[2]:.3f}%',f'MFE={x[3]:.3f}%',f'MAE={x[4]:.3f}%',f'HOLD={x[5]}m',x[6],f'PEAK={x[7]:.3f}%')
    con.close()
if __name__=='__main__': main()
