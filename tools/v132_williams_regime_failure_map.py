#!/usr/bin/env python3
"""V132 Williams regime failure map.
READ ONLY. NO ORDERS. NO DOWNLOADS.
Purpose: explain OOS/HOLDOUT instability by causal market-regime buckets.
Uses fixed T5_02_HIST_VWAP + LOCKTRAIL + 1.5 stop baseline and maps trade edge
by QQQ/SPY intraday trend, opening gap, volatility and directional regime.
"""
from __future__ import annotations
import sqlite3, statistics
from collections import defaultdict
from pathlib import Path

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
INV={'SOXS','SQQQ'}

def ema(v,n):
    if not v:return []
    a=2/(n+1);o=[float(v[0])]
    for x in v[1:]:o.append(a*float(x)+(1-a)*o[-1])
    return o

def rsi(v,n=2):
    o=[None]*len(v)
    if len(v)<n+2:return o
    g=[];l=[]
    for i in range(1,n+1):
        d=v[i]-v[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/n;al=sum(l)/n;o[n]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(n+1,len(v)):
        d=v[i]-v[i-1];gg=max(d,0);ll=max(-d,0);ag=(ag*(n-1)+gg)/n;al=(al*(n-1)+ll)/n;o[i]=100 if al==0 else 100-100/(1+ag/al)
    return o

def pct(a,b):return (b/a-1)*100 if a else 0.0

def load(con,s,d):
    return con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()

def dates(con,s):return [r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date",(s,))]

def arrays(rows):
    H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows];V=[float(x[5] or 0) for x in rows]
    e12=ema(C,12);e26=ema(C,26);hist=[a-b for a,b in zip(e12,e26)];r2=rsi(C,2)
    vw=[];pv=vv=0.0
    for h,l,c,v in zip(H,L,C,V):
        tp=(h+l+c)/3;pv+=tp*v;vv+=v;vw.append(pv/vv if vv else c)
    return H,L,C,V,hist,r2,vw

def entry(prev,cur):
    if len(prev)<100 or len(cur)<100:return None
    ph=max(float(x[2]) for x in prev);pl=min(float(x[3]) for x in prev);op=float(cur[0][1]);tr=op+0.5*(ph-pl)
    H,L,C,V,hist,r2,vw=arrays(cur)
    for i in range(20,len(C)-10):
        if not(C[i-1]<=tr<C[i]) or r2[i] is None or r2[i]<=50:continue
        j=i+5
        if pct(C[i],C[j])<0.20:continue
        if not(hist[j]>hist[i]):continue
        if not all(C[k]>=vw[k] for k in range(i+1,j+1)):continue
        return {'i':j,'e':C[j],'H':H,'L':L,'C':C}
    return None

def trade(e):
    i0=e['i'];en=e['e'];H=e['H'];C=e['C'];peak=en
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]);pr=pct(en,peak);cur=pct(en,C[i]);dd=pct(peak,C[i])
        if cur<=-1.5:return pct(en,C[i])
        if pr>=0.30 and cur<=0:return pct(en,C[i])
        if pr>=0.50 and cur<=0.20:return pct(en,C[i])
        if pr>=0.80 and dd<=-0.30:return pct(en,C[i])
    return pct(en,C[-1])

def regime(rows,prev):
    if len(rows)<35 or len(prev)<50:return None
    O=float(rows[0][1]);C=[float(x[4]) for x in rows];H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows]
    pc=float(prev[-1][4]);gap=pct(pc,O);r30=pct(O,C[30]);rng=pct(O,max(H[:31]))-pct(O,min(L[:31]))
    e9=ema(C[:31],9)[-1];e20=ema(C[:31],20)[-1]
    trend='UP' if C[30]>e9>e20 else ('DOWN' if C[30]<e9<e20 else 'MIXED')
    vol='HIGHVOL' if rng>=1.0 else ('MIDVOL' if rng>=0.5 else 'LOWVOL')
    gapb='GAPUP' if gap>=0.3 else ('GAPDN' if gap<=-0.3 else 'FLATGAP')
    mom='STRONGUP' if r30>=0.5 else ('STRONGDN' if r30<=-0.5 else 'NEUTRAL')
    return trend,vol,gapb,mom

def met(rs):
    if not rs:return None
    net=[x-0.08 for x in rs];w=[x for x in net if x>0];l=[x for x in net if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl>0 else (999 if gp>0 else 0)
    return len(net),100*len(w)/len(net),statistics.fmean(net),pf,sum(net)

def main():
    con=sqlite3.connect(DB)
    qd=dates(con,'QQQ'); common=[]
    for d in qd:
        q=load(con,'QQQ',d);sp=load(con,'SPY',d)
        if len(q)>=300 and len(sp)>=300:common.append(str(d))
    common=common[-135:];n=len(common);a=int(n*.6);b=int(n*.8);lab={d:('IS' if i<a else ('OOS' if i<b else 'HOLDOUT')) for i,d in enumerate(common)}
    maps=defaultdict(list);total=defaultdict(list)
    for s in SYMS:
        ds=dates(con,s);dset=set(map(str,ds))
        prevmap={str(ds[i]):str(ds[i-1]) for i in range(1,len(ds))}
        for d in common:
            if d not in dset or d not in prevmap:continue
            cur=load(con,s,d);prev=load(con,s,prevmap[d]);e=entry(prev,cur)
            if not e:continue
            qq=load(con,'QQQ',d);qprev=load(con,'QQQ',prevmap[d]) if prevmap[d] in set(map(str,dates(con,'QQQ'))) else []
            rr=regime(qq,qprev)
            if not rr:continue
            ret=trade(e); total[lab[d]].append(ret)
            trend,vol,gapb,mom=rr
            for k in [('TREND',trend),('VOL',vol),('GAP',gapb),('MOM',mom),('COMBO',trend+'|'+vol+'|'+mom)]:maps[(lab[d],)+k].append(ret)
    con.close()
    print('=== V132 REGIME FAILURE MAP ===')
    for z in ('IS','OOS','HOLDOUT'):print(z,'TOTAL',met(total[z]))
    for typ in ('TREND','VOL','GAP','MOM','COMBO'):
        print('\n--',typ,'--')
        vals=sorted(set(k[2] for k in maps if k[1]==typ))
        for v in vals:
            print(v,'OOS',met(maps.get(('OOS',typ,v),[])),'HOLDOUT',met(maps.get(('HOLDOUT',typ,v),[])))
    print('\nNEXT=USE_ONLY_REGIMES_WITH_SAME_SIGN_EDGE_IN_OOS_AND_HOLDOUT; DO_NOT_TUNE_ON_HOLDOUT ALONE')
if __name__=='__main__':main()
