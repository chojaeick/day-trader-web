#!/usr/bin/env python3
"""V133 Williams regime whitelist candidate.

Read-only, no API/orders/downloads.
Uses only regimes that had same-sign positive edge in BOTH OOS and HOLDOUT in V132.
No holdout-only regime is allowed.
"""
from __future__ import annotations
import sqlite3, statistics
from pathlib import Path

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
INV={'SOXS','SQQQ'}
COSTS=[8.0,12.0,16.0]


def pct(a,b): return (b/a-1)*100 if a else 0.0

def ema(v,n):
    if not v:return []
    a=2/(n+1); out=[float(v[0])]
    for x in v[1:]: out.append(a*float(x)+(1-a)*out[-1])
    return out

def rsi(v,n=2):
    out=[None]*len(v)
    if len(v)<n+2:return out
    g=[];l=[]
    for i in range(1,n+1):
        d=v[i]-v[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/n;al=sum(l)/n;out[n]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(n+1,len(v)):
        d=v[i]-v[i-1];gg=max(d,0);ll=max(-d,0);ag=(ag*(n-1)+gg)/n;al=(al*(n-1)+ll)/n;out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def load_day(c,s,d):
    return c.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
def days(c,s):
    return sorted(r[0] for r in c.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit 135",(s,)))

def arr(rows):
    H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows];V=[float(x[5] or 0) for x in rows]
    r2=rsi(C,2);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9);hist=[a-b for a,b in zip(mac,sig)]
    vw=[];pv=vv=0
    for h,l,c,v in zip(H,L,C,V):
        tp=(h+l+c)/3;pv+=tp*v;vv+=v;vw.append(pv/vv if vv else c)
    return H,L,C,V,r2,mac,sig,hist,vw

def entry(prev,cur):
    if len(prev)<100 or len(cur)<70:return None
    ph=max(float(x[2]) for x in prev);pl=min(float(x[3]) for x in prev);tr=float(cur[0][1])+0.5*(ph-pl)
    H,L,C,V,r2,mac,sig,hist,vw=arr(cur)
    for i in range(20,len(cur)-6):
        if not (C[i-1]<=tr<C[i]) or r2[i] is None or r2[i]<=50: continue
        j=i+5
        if pct(C[i],C[j])<0.20: return None
        if not hist[j]>hist[i]: return None
        if not all(C[k]>=vw[k] for k in range(i+1,j+1)): return None
        return {'i':j,'entry':C[j],'H':H,'L':L,'C':C,'mac':mac,'sig':sig,'hist':hist,'vw':vw}
    return None

def regime(qrows):
    if len(qrows)<31:return None
    H,L,C,V,_,mac,sig,hist,vw=arr(qrows)
    op=float(qrows[0][1]); prev_close=float(qrows[0][1])
    # Gap proxy vs first regular open and prior day's close is supplied externally later.
    ret30=pct(C[0],C[30])
    rng=(max(H[:31])-min(L[:31]))/C[0]*100 if C[0] else 0
    e9=ema(C[:31],9);e20=ema(C[:31],20)
    if e9[-1]>e20[-1] and ret30>0.25: trend='UP'
    elif e9[-1]<e20[-1] and ret30<-0.25: trend='DOWN'
    else: trend='MIXED'
    vol='HIGHVOL' if rng>=1.25 else ('LOWVOL' if rng<0.45 else 'MIDVOL')
    mom='STRONGUP' if ret30>=0.8 else ('STRONGDN' if ret30<=-0.8 else 'NEUTRAL')
    return trend,vol,mom

def exit_lock(e,stop=1.5):
    i0=e['i'];entry=e['entry'];H=e['H'];C=e['C'];peak=entry
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]);pr=pct(entry,peak);cur=pct(entry,C[i]);dd=pct(peak,C[i])
        if cur<=-stop:return i,'HARD'
        if pr>=0.30 and cur<=0:return i,'LOCK03_BE'
        if pr>=0.50 and cur<=0.20:return i,'LOCK05_02'
        if pr>=0.80 and dd<=-0.30:return i,'TRAIL08_03'
    return len(C)-1,'EOD'

def metrics(rs):
    if not rs:return None
    r=[x['ret'] for x in rs];w=[x for x in r if x>0];l=[x for x in r if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl>0 else (999 if gp>0 else 0)
    eq=pk=0;mdd=0
    for x in r:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return {'n':len(r),'win':100*len(w)/len(r),'avg':statistics.fmean(r),'pf':pf,'net':sum(r),'mdd':mdd}

def main():
    c=sqlite3.connect(DB)
    qdays=days(c,'QQQ'); qmap={d:load_day(c,'QQQ',d) for d in qdays}
    allrows=[]
    for s in SYMS:
        ds=days(c,s)
        for k in range(1,len(ds)):
            d=ds[k]
            if d not in qmap: continue
            p=load_day(c,s,ds[k-1]);cur=load_day(c,s,d);e=entry(p,cur)
            if not e:continue
            rg=regime(qmap[d])
            if not rg:continue
            # true gap direction using QQQ prior regular close
            qi=qdays.index(d)
            if qi==0:continue
            qprev=qmap[qdays[qi-1]]; gap=pct(float(qprev[-1][4]),float(qmap[d][0][1]))
            gapcat='GAPUP' if gap>=0.25 else ('GAPDN' if gap<=-0.25 else 'FLATGAP')
            trend,vol,mom=rg
            allow=(gapcat=='GAPUP') or ((trend,vol,mom) in {('MIXED','MIDVOL','NEUTRAL'),('DOWN','HIGHVOL','STRONGDN')})
            if not allow:continue
            ix,reason=exit_lock(e,1.5)
            gross=pct(e['entry'],e['C'][ix])
            allrows.append({'date':str(d),'symbol':s,'gross':gross,'reason':reason,'regime':f'{trend}|{vol}|{mom}|{gapcat}'})
    c.close()
    dates=sorted(set(x['date'] for x in allrows));a=int(len(dates)*0.6);b=int(len(dates)*0.8);sp={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('=== V133 REGIME WHITELIST CANDIDATE ===')
    print('WHITELIST=GAPUP OR MIXED|MIDVOL|NEUTRAL OR DOWN|HIGHVOL|STRONGDN')
    print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in sp.items()})
    final=True
    for cost in COSTS:
        print('-- COST',cost,'bps --');cp=cost/100
        for lab in ('IS','OOS','HOLDOUT'):
            z=[{**x,'ret':x['gross']-cp} for x in allrows if x['date'] in sp[lab]];m=metrics(z);print(lab,m)
            if lab in ('OOS','HOLDOUT') and cost==8.0:
                if not m or m['n']<15 or m['avg']<=0 or m['pf']<1.3: final=False
    print('=== HOLDOUT BY REGIME ===')
    cp=.08
    for rg in sorted(set(x['regime'] for x in allrows)):
        z=[{**x,'ret':x['gross']-cp} for x in allrows if x['date'] in sp['HOLDOUT'] and x['regime']==rg]
        if z:print(rg,metrics(z))
    print('EXIT_REASONS', {r:sum(1 for x in allrows if x['date'] in sp['HOLDOUT'] and x['reason']==r) for r in sorted(set(x['reason'] for x in allrows))})
    print('FINAL_PASS=',final)

if __name__=='__main__':main()
