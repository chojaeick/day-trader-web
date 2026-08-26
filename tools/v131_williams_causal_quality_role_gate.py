#!/usr/bin/env python3
"""V131 Williams causal rolling-quality / role-gate audit.

READ ONLY. NO API. NO ORDERS. NO DOWNLOADS.

Baseline is the V130 winner concept:
  ENTRY  = first Williams arrow -> +0.20% after 5m + MACD hist accel + VWAP hold
  TREND  = symbol trend gate at entry
  EXIT   = LOCKTRAIL
  HARD   = -1.50%

This script does NOT cherry-pick HOLDOUT symbols. It asks whether prior, already
closed trades can causally tell us when a symbol / role should be trusted.
Every quality decision uses only trades strictly earlier than the candidate.
"""
from __future__ import annotations
import argparse, sqlite3, statistics, math, time
from collections import defaultdict, deque
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
DB0=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
INVERSE={'SOXS','SQQQ'}
LEV_LONG={'SOXL','TQQQ'}
INDEX={'QQQ','SPY','SMH'}

def role(s):
    if s in INVERSE:return 'INVERSE'
    if s in LEV_LONG:return 'LEV_LONG'
    if s in INDEX:return 'INDEX'
    return 'SINGLE'

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

def hhmm(x):
    s=str(x)
    if 'T' in s:s=s.split('T',1)[1]
    if ':' in s:
        p=s[:5].split(':');return int(p[0])*100+int(p[1])
    d=''.join(c for c in s if c.isdigit())
    return int(d[-6:-2]) if len(d)>=6 else None

def load(con,s,maxd):
    ds=[str(r[0]) for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(s,maxd+1))]
    out={}
    for d in sorted(ds):
        q=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(q)>=300:out[d]=q
    return out

def arrays(rows):
    H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows];V=[float(x[5] or 0) for x in rows]
    r2=rsi(C,2);e9=ema(C,9);e20=ema(C,20);e12=ema(C,12);e26=ema(C,26);macd=[a-b for a,b in zip(e12,e26)];sig=ema(macd,9);hist=[a-b for a,b in zip(macd,sig)]
    vw=[];pv=vv=0.0
    for h,l,c,v in zip(H,L,C,V):
        tp=(h+l+c)/3;pv+=tp*v;vv+=v;vw.append(pv/vv if vv else c)
    return H,L,C,V,r2,e9,e20,hist,vw

def candidate(prev,cur):
    if len(prev)<100 or len(cur)<70:return None
    ph=max(float(x[2]) for x in prev);pl=min(float(x[3]) for x in prev);op=float(cur[0][1]);trig=op+0.5*(ph-pl)
    H,L,C,V,r2,e9,e20,hist,vw=arrays(cur)
    arrow=None
    for i in range(20,len(cur)-8):
        if C[i-1]<=trig<C[i] and r2[i] is not None and r2[i]>50:
            arrow=i;break
    if arrow is None:return None
    j=arrow+5
    if pct(C[arrow],C[j])<0.20:return None
    if not (hist[j]>hist[arrow]):return None
    if not all(C[k]>=vw[k] for k in range(arrow+1,j+1)):return None
    # V130 symbol trend gate: do not enter a broadly deteriorating local trend.
    trend_ok=(C[j]>=e20[j] and e9[j]>=e20[j] and C[j]>=C[max(0,j-5)])
    if not trend_ok:return None
    return {'i':j,'entry':C[j],'H':H,'L':L,'C':C,'vw':vw,'hist':hist}

def exit_trade(e,hard=1.5):
    i0=e['i'];entry=e['entry'];H=e['H'];L=e['L'];C=e['C'];peak=entry;reason='EOD';ix=len(C)-1
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]);pr=pct(entry,peak);cur=pct(entry,C[i]);dd=pct(peak,C[i])
        if cur<=-hard:ix=i;reason='HARD';break
        # deliberately loose profit protection; normal pullbacks are allowed.
        if pr>=0.30 and cur<=0.00:ix=i;reason='LOCK03_BE';break
        if pr>=0.50 and cur<=0.20:ix=i;reason='LOCK05_02';break
        if pr>=0.80 and dd<=-0.30:ix=i;reason='TRAIL08_03';break
    return {'gross':pct(entry,C[ix]),'mfe':pct(entry,max(H[i0:ix+1])),'mae':pct(entry,min(L[i0:ix+1])),'gb':max(0,pct(entry,max(H[i0:ix+1]))-pct(entry,C[ix])),'reason':reason}

def met(ts,cost):
    if not ts:return None
    r=[x['gross']-cost for x in ts];w=[x for x in r if x>0];l=[x for x in r if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl>0 else (999 if gp>0 else 0)
    eq=pk=0;mdd=0
    for x in r:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return {'n':len(r),'win':100*len(w)/len(r),'avg':statistics.fmean(r),'pf':pf,'net':sum(r),'mdd':mdd,'gb':statistics.fmean([x['gb'] for x in ts])}

def pfavg(hist,cost=.08):
    if not hist:return (None,None,None)
    r=[x-cost for x in hist];w=[x for x in r if x>0];l=[x for x in r if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl>0 else (999 if gp>0 else 0)
    return pf,statistics.fmean(r),100*len(w)/len(r)

def allow(mode,s,symhist,rolehist):
    sh=list(symhist[s]);rh=list(rolehist[role(s)])
    if mode=='NONE':return True
    if mode=='SYM5_AVG':
        if len(sh)<3:return True
        _,a,_=pfavg(sh[-5:]);return a is not None and a>0
    if mode=='SYM8_PF':
        if len(sh)<4:return True
        p,a,w=pfavg(sh[-8:]);return p is not None and p>=1.05 and a>=0
    if mode=='SYM8_WIN50':
        if len(sh)<4:return True
        p,a,w=pfavg(sh[-8:]);return w is not None and w>=50 and a>=0
    if mode=='ROLE12_AVG':
        if len(rh)<6:return True
        _,a,_=pfavg(rh[-12:]);return a is not None and a>0
    if mode=='SYM_ROLE':
        sok=True;rok=True
        if len(sh)>=4:
            p,a,w=pfavg(sh[-8:]);sok=(a>=0 and p>=1.0)
        if len(rh)>=6:
            p,a,w=pfavg(rh[-12:]);rok=(a>=0)
        return sok and rok
    return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(DB0));ap.add_argument('--max-days',type=int,default=135);args=ap.parse_args();t0=time.time()
    con=sqlite3.connect(args.db);raw=[]
    for s in SYMS:
        dm=load(con,s,args.max_days);ds=sorted(dm);n=0
        for z in range(1,len(ds)):
            e=candidate(dm[ds[z-1]],dm[ds[z]])
            if not e:continue
            tr=exit_trade(e,1.5);tr.update(symbol=s,date=ds[z],role=role(s));raw.append(tr);n+=1
        print('LOAD',s,'ROLE',role(s),'TRADES',n)
    con.close();raw.sort(key=lambda x:(x['date'],x['symbol']))
    dates=sorted(set(x['date'] for x in raw));a=int(len(dates)*.60);b=int(len(dates)*.80);sp={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('\n=== V131 CAUSAL QUALITY / ROLE GATE ===');print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE');print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in sp.items()})
    modes=['NONE','SYM5_AVG','SYM8_PF','SYM8_WIN50','ROLE12_AVG','SYM_ROLE'];results={}
    for mode in modes:
        sh=defaultdict(lambda:deque(maxlen=20));rh=defaultdict(lambda:deque(maxlen=40));kept=[];blocked=0
        for x in raw:
            ok=allow(mode,x['symbol'],sh,rh)
            if ok:kept.append(x)
            else:blocked+=1
            # critical: history learns every completed baseline trade, including blocked paper opportunities.
            sh[x['symbol']].append(x['gross']);rh[x['role']].append(x['gross'])
        results[mode]=kept
        o=met([x for x in kept if x['date'] in sp['OOS']],.08);h=met([x for x in kept if x['date'] in sp['HOLDOUT']],.08)
        print(mode,'BLOCKED',blocked,'OOS',o,'HOLDOUT',h)
    eligible=[]
    for m,ts in results.items():
        o=met([x for x in ts if x['date'] in sp['OOS']],.08);h=met([x for x in ts if x['date'] in sp['HOLDOUT']],.08)
        if o and h and o['n']>=15 and h['n']>=15 and o['avg']>0 and h['avg']>0 and o['pf']>=1.15 and h['pf']>=1.15:
            score=min(o['pf'],h['pf'])+.5*min(o['avg'],h['avg'])+.002*min(o['win'],h['win']);eligible.append((score,m,o,h))
    eligible.sort(reverse=True)
    print('\n=== ROBUST RANK ===')
    if not eligible:print('NO_ELIGIBLE_CANDIDATE')
    for i,(sc,m,o,h) in enumerate(eligible,1):print(i,m,'SCORE',round(sc,4),'OOS',o,'HOLDOUT',h)
    if eligible:
        _,winm,o,h=eligible[0];ts=results[winm];print('\nWINNER',winm)
        print('=== COST STRESS ===')
        for bps in (8,12,16):
            c=bps/100;print('COST',bps,'OOS',met([x for x in ts if x['date'] in sp['OOS']],c),'HOLDOUT',met([x for x in ts if x['date'] in sp['HOLDOUT']],c))
        print('=== HOLDOUT BY ROLE ===')
        for r in ('SINGLE','INDEX','LEV_LONG','INVERSE'):
            print(r,met([x for x in ts if x['date'] in sp['HOLDOUT'] and x['role']==r],.08))
        final=(o['pf']>=1.3 and h['pf']>=1.3 and o['avg']>0 and h['avg']>0)
        print('FINAL_PASS=',final)
    else:print('FINAL_PASS= False')
    print('ELAPSED_SEC',round(time.time()-t0,1))
if __name__=='__main__':main()
