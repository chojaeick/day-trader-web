#!/usr/bin/env python3
"""V136 Williams winner robustness test.
READ ONLY / NO ORDERS / NO DOWNLOADS / STDLIB ONLY.

Re-tests the V135 Williams winner without changing the live engine.
Checks:
- same chronological 60/20/20 split
- cost stress 8/12/16 bps
- local parameter perturbation around V135 baseline
- leave-one-symbol-out robustness
- four chronological blocks

Baseline copied from V135:
first Williams cross, 09:30-11:00, volume>=1.5x prior10, RSI2>50,
CCI20>100, MACD histogram rising, COMBO2 exit, 1.0% hard stop.
"""
from __future__ import annotations
import argparse, sqlite3, statistics, itertools, time
from collections import defaultdict
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
DB_DEFAULT=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
COSTS=[0.08,0.12,0.16]
BASE=(1.5,100.0,1.0,2)

def pct(a,b): return (b/a-1.0)*100.0 if a else 0.0

def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals,p):
    n=len(vals); out=[None]*n
    if n<p+2:return out
    gs=[];ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1];gs.append(max(d,0));ls.append(max(-d,0))
    ag=sum(gs)/p;al=sum(ls)/p;out[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,n):
        d=vals[i]-vals[i-1];g=max(d,0);l=max(-d,0)
        ag=(ag*(p-1)+g)/p;al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def cci(H,L,C,p=20):
    tp=[(h+l+c)/3.0 for h,l,c in zip(H,L,C)]; out=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1];m=sum(w)/p;md=sum(abs(x-m) for x in w)/p
        out[i]=0.0 if md==0 else (tp[i]-m)/(0.015*md)
    return out

def hhmm(x):
    s=str(x)
    if 'T' in s:s=s.split('T',1)[1]
    if ':' in s:
        try:return int(s[:2])*100+int(s[3:5])
        except:return None
    d=''.join(ch for ch in s if ch.isdigit())
    return int(d[-6:-2]) if len(d)>=6 else None

def load_days(con,sym,max_days):
    ds=[str(r[0]) for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(sym,max_days+1)).fetchall()]
    out={}
    for d in sorted(ds):
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(sym,d)).fetchall()
        if len(rows)>=300:out[d]=rows
    return out

def prep(prev,cur):
    if len(prev)<100 or len(cur)<40:return None
    H=[float(r[2]) for r in cur];L=[float(r[3]) for r in cur];C=[float(r[4]) for r in cur];V=[float(r[5] or 0) for r in cur]
    ph=max(float(r[2]) for r in prev);pl=min(float(r[3]) for r in prev);trig=float(cur[0][1])+0.5*(ph-pl)
    r2=rsi(C,2);cc=cci(H,L,C,20);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9);hist=[a-b for a,b in zip(mac,sig)]
    return cur,H,L,C,V,trig,r2,cc,mac,sig,hist

def simulate(p,vol_mult,cci_min,hard_stop,combo_bars):
    if not p:return None
    cur,H,L,C,V,trig,r2,cc,mac,sig,hist=p
    first_seen=False
    for i in range(20,len(cur)-2):
        cross=C[i-1]<=trig<C[i]
        if not cross or r2[i] is None or r2[i]<=50:continue
        if first_seen:continue
        first_seen=True
        t=hhmm(cur[i][0]);prior=V[max(0,i-10):i];va=sum(prior)/len(prior) if prior else 0.0
        if t is None or not (930<=t<=1100):return None
        if va<=0 or V[i] < vol_mult*va:return None
        if cc[i] is None or cc[i] <= cci_min:return None
        if i<1 or hist[i] <= hist[i-1]:return None
        entry=C[i];weak=0;ix=len(C)-1;reason='EOD'
        for j in range(i+1,len(C)):
            if pct(entry,C[j]) <= -hard_stop:
                ix=j;reason='HARD';break
            cdown=cc[j] is not None and cc[j-1] is not None and cc[j]<cc[j-1]
            combo=mac[j]<sig[j] and cdown
            weak=weak+1 if combo else 0
            if weak>=combo_bars:
                ix=j;reason=f'COMBO{combo_bars}';break
        return {'gross':pct(entry,C[ix]),'entry_i':i,'exit_i':ix,'reason':reason}
    return None

def metrics(trades,cost):
    if not trades:return None
    rs=[x['gross']-cost for x in trades]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    gp=sum(wins);gl=-sum(losses);pf=gp/gl if gl>0 else (999.0 if gp>0 else 0.0)
    eq=peak=0.0;mdd=0.0
    for x in rs:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
    return {'n':len(rs),'win':100*len(wins)/len(rs),'avg':statistics.fmean(rs),'pf':pf,'net':sum(rs),'mdd':mdd}

def good(z): return bool(z and z['n']>=10 and z['avg']>0 and z['pf']>1.0)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(DB_DEFAULT));ap.add_argument('--max-days',type=int,default=135);args=ap.parse_args();t0=time.time()
    con=sqlite3.connect(args.db); prepared=[]; all_dates=set()
    for sym in SYMS:
        dm=load_days(con,sym,args.max_days);ds=sorted(dm);n=0
        for i in range(1,len(ds)):
            p=prep(dm[ds[i-1]],dm[ds[i]])
            prepared.append((ds[i],sym,p));all_dates.add(ds[i]);n+=1
        print('LOAD',sym,'DAYS=',n)
    con.close()
    dates=sorted(all_dates);a=int(len(dates)*.60);b=int(len(dates)*.80)
    sets={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('\n=== V136 WILLIAMS WINNER ROBUSTNESS ===')
    print('BASELINE vol=1.5 cci=100 hard=1.0 combo=2')
    print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in sets.items()})

    params=list(itertools.product([1.3,1.5,1.7],[80.0,100.0,120.0],[0.8,1.0,1.2],[1,2,3]))
    result={}
    for par in params:
        by={'IS':[],'OOS':[],'HOLDOUT':[]}
        for d,s,p in prepared:
            t=simulate(p,*par)
            if not t:continue
            lab='IS' if d in sets['IS'] else ('OOS' if d in sets['OOS'] else 'HOLDOUT')
            by[lab].append({'date':d,'symbol':s,**t})
        result[par]=by

    print('\n=== BASELINE COST STRESS ===')
    base=result[BASE]
    for c in COSTS:
        print('COST',c,'IS',metrics(base['IS'],c),'OOS',metrics(base['OOS'],c),'HOLDOUT',metrics(base['HOLDOUT'],c))

    stable=[]
    for par,by in result.items():
        o=metrics(by['OOS'],.08);h=metrics(by['HOLDOUT'],.08)
        if good(o) and good(h):stable.append((par,o,h))
    print('\n=== LOCAL PERTURBATION STABILITY @8bps ===')
    print('TOTAL_VARIANTS',len(params),'ROBUST_POSITIVE',len(stable),'RATIO',f'{100*len(stable)/len(params):.1f}%')
    stable.sort(key=lambda x:(x[1]['pf']+x[2]['pf'],x[1]['avg']+x[2]['avg']),reverse=True)
    for i,(par,o,h) in enumerate(stable[:15],1):print(i,'PARAM',par,'OOS',o,'HOLDOUT',h)

    print('\n=== BASELINE LEAVE-ONE-SYMBOL-OUT @8bps ===')
    loso_pass=0
    for drop in SYMS:
        o=metrics([x for x in base['OOS'] if x['symbol']!=drop],.08);h=metrics([x for x in base['HOLDOUT'] if x['symbol']!=drop],.08)
        ok=good(o) and good(h);loso_pass+=int(ok)
        print(drop,'PASS',ok,'OOS',o,'HOLDOUT',h)

    print('\n=== BASELINE 4 CHRONO BLOCKS @8bps ===')
    block_pass=0
    allbase=base['IS']+base['OOS']+base['HOLDOUT']
    for bi in range(4):
        lo=int(len(dates)*bi/4);hi=int(len(dates)*(bi+1)/4);ds=set(dates[lo:hi]);z=metrics([x for x in allbase if x['date'] in ds],.08);ok=good(z);block_pass+=int(ok);print('BLOCK',bi+1,'DATES',len(ds),'PASS',ok,z)

    o16=metrics(base['OOS'],.16);h16=metrics(base['HOLDOUT'],.16)
    perturb_ratio=len(stable)/len(params)
    final_pass=bool(good(o16) and good(h16) and perturb_ratio>=0.35 and loso_pass>=15 and block_pass>=3)
    print('\nPERTURB_PASS_RATIO',f'{perturb_ratio:.3f}','LOSO_PASS',f'{loso_pass}/{len(SYMS)}','BLOCK_PASS',f'{block_pass}/4')
    print('FINAL_PASS=',final_pass)
    print('NEXT=', 'FREEZE_STRATEGY_AND_BUILD_LIVE_REPLICATION_AUDIT' if final_pass else 'DO_NOT_DEPLOY; IDENTIFY_INSTABILITY_SOURCE')
    print('ELAPSED_SEC',round(time.time()-t0,1))

if __name__=='__main__':main()
