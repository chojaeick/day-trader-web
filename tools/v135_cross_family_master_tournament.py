#!/usr/bin/env python3
"""V135 cross-family master tournament.
READ ONLY / NO ORDERS / NO DOWNLOADS.

Goal: compare representative executable strategy families on one US master DB
with the same chronological split and same friction assumptions.

Families implemented here from existing repository logic:
- WILLIAMS: V5 strict entry + COMBO2 exit, 1.0% hard stop.
- MA20_GAP: gap reversion architecture from ma20_scalp_backtest_v01.
- MA20_PULLBACK: trend pullback architecture from ma20_scalp_backtest_v01.
- FUJIMOTO_F2/F4: staged Fujimoto reimplementation baseline.
- ETHAN_QQQ: source-constrained breakout/retest architecture, QQQ only.

Important:
- This is a comparison harness, not a claim that every external strategy is
  perfectly reproduced. Ethan/Fujimoto remain repository reimplementations.
- No live-engine code imported or modified.
"""
from __future__ import annotations
import argparse, math, sqlite3, statistics, json, time
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT=Path('/home/ubuntu/day-trader-api')
DB_DEFAULT=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
COSTS=[0.08,0.12,0.16]

def pct(a,b): return (b/a-1)*100 if a else 0.0

def ema(vals,span):
    if not vals:return []
    a=2/(span+1);out=[float(vals[0])]
    for v in vals[1:]:out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals,p):
    n=len(vals);out=[None]*n
    if n<p+2:return out
    gs=[];ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1];gs.append(max(d,0));ls.append(max(-d,0))
    ag=sum(gs)/p;al=sum(ls)/p;out[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,n):
        d=vals[i]-vals[i-1];g=max(d,0);l=max(-d,0);ag=(ag*(p-1)+g)/p;al=(al*(p-1)+l)/p;out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def cci(H,L,C,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)];o=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1];m=sum(w)/p;md=sum(abs(x-m) for x in w)/p;o[i]=0 if md==0 else (tp[i]-m)/(0.015*md)
    return o

def hhmm(x):
    s=str(x)
    if 'T' in s:s=s.split('T',1)[1]
    if ':' in s:return int(s[:2])*100+int(s[3:5])
    d=''.join(c for c in s if c.isdigit());return int(d[-6:-2]) if len(d)>=6 else None

def load_days(con,sym,max_days=135):
    ds=[str(r[0]) for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(sym,max_days+1)).fetchall()]
    out={}
    for d in sorted(ds):
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(sym,d)).fetchall()
        if len(rows)>=300:out[d]=rows
    return out

def metrics(trades,cost):
    if not trades:return None
    rs=[t['gross']-cost for t in trades];w=[x for x in rs if x>0];l=[x for x in rs if x<0]
    gp=sum(w);gl=-sum(l);pf=gp/gl if gl>0 else (999 if gp>0 else 0)
    eq=peak=0;mdd=0
    for x in rs:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
    return {'n':len(rs),'win':100*len(w)/len(rs),'avg':statistics.fmean(rs),'pf':pf,'net':sum(rs),'mdd':mdd}

# -------- Williams --------
def williams_day(prev,cur):
    if len(prev)<100 or len(cur)<40:return None
    H=[float(r[2]) for r in cur];L=[float(r[3]) for r in cur];C=[float(r[4]) for r in cur];V=[float(r[5] or 0) for r in cur]
    ph=max(float(r[2]) for r in prev);pl=min(float(r[3]) for r in prev);trig=float(cur[0][1])+0.5*(ph-pl)
    r2=rsi(C,2);cc=cci(H,L,C,20);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9)
    first_seen=False
    for i in range(20,len(cur)-2):
        cross=C[i-1]<=trig<C[i]
        if not cross or r2[i] is None or r2[i]<=50:continue
        if first_seen:continue
        first_seen=True;t=hhmm(cur[i][0]);prior=V[max(0,i-10):i];va=sum(prior)/len(prior) if prior else 0
        if t is None or not (930<=t<=1100) or va<=0 or V[i]<1.5*va or cc[i] is None or cc[i]<=100 or mac[i]-sig[i] <= mac[i-1]-sig[i-1]:return None
        weak=0;entry=C[i];ix=len(C)-1
        for j in range(i+1,len(C)):
            if pct(entry,C[j])<=-1.0:ix=j;break
            cd=cc[j] is not None and cc[j-1] is not None and cc[j]<cc[j-1];combo=mac[j]<sig[j] and cd;weak=weak+1 if combo else 0
            if weak>=2:ix=j;break
        return {'gross':pct(entry,C[ix])}
    return None

# -------- MA20 --------
def ma20_day(cur):
    df=pd.DataFrame(cur,columns=['time','open','high','low','close','volume']).astype({'open':float,'high':float,'low':float,'close':float,'volume':float})
    df['ma20']=df.close.rolling(20,min_periods=20).mean();df['slope']=df.ma20-df.ma20.shift(3);df['gap']=(df.ma20-df.close)/df.ma20*100;df['bull']=df.close>df.open
    out=[]
    # A gap reversion, defaults from v01 except common cost removed here.
    i=21
    while i<len(df)-1:
        if pd.isna(df.loc[i,'ma20']):i+=1;continue
        gap=float(df.loc[i,'gap']);pg=float(df.loc[i-1,'gap']);px=float(df.loc[i,'close']);ma=float(df.loc[i,'ma20']);target=px+(ma-px)*0.25
        if gap>=2.0 and gap<pg and pct(px,target)>=0.50:
            end=min(len(df)-1,i+30);entrygap=gap;xi=end;xp=float(df.loc[end,'close'])
            for j in range(i+1,end+1):
                if float(df.loc[j,'high'])>=target:xi=j;xp=target;break
                if float(df.loc[j,'gap'])>=entrygap+0.75:xi=j;xp=float(df.loc[j,'close']);break
            out.append(('MA20_GAP',{'gross':pct(px,xp)}));i=xi+1
        else:i+=1
    # B pullback
    i=22
    while i<len(df)-1:
        if pd.isna(df.loc[i,'ma20']):i+=1;continue
        w=5
        if i<2*w+1:i+=1;continue
        low1=float(df.loc[i-2*w:i-w-1,'low'].min());low2=float(df.loc[i-w:i-1,'low'].min());px=float(df.loc[i,'close']);ma=float(df.loc[i,'ma20'])
        cond=float(df.loc[i,'slope'])>0 and low2>low1 and abs(px/ma-1)*100<=0.75 and bool(df.loc[i,'bull']) and px>float(df.loc[i-1,'close'])
        if not cond:i+=1;continue
        target=float(df.loc[max(0,i-10):i-1,'high'].max());stop=low2
        if target<=px or stop>=px:i+=1;continue
        end=min(len(df)-1,i+30);xi=end;xp=float(df.loc[end,'close'])
        for j in range(i+1,end+1):
            if float(df.loc[j,'low'])<=stop:xi=j;xp=stop;break
            if float(df.loc[j,'high'])>=target:xi=j;xp=target;break
        out.append(('MA20_PULLBACK',{'gross':pct(px,xp)}));i=xi+1
    return out

# -------- Fujimoto simplified exact staged logic per day 5m --------
def fujimoto_day(cur):
    q=pd.DataFrame(cur,columns=['time','open','high','low','close','volume']);q['bucket']=np.arange(len(q))//5
    x=q.groupby('bucket').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna().astype(float)
    if len(x)<70:return []
    d=x.close.diff();up=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();dn=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean();rs=up/dn.replace(0,np.nan);x['rsi']=100-100/(1+rs);x['ema8']=x.close.ewm(span=8,adjust=False).mean();x['ema20']=x.close.ewm(span=20,adjust=False).mean()
    # causal pivot confirmation: pivot at k only known at k+2.
    piv=[]
    for k in range(2,len(x)-2):
        if x.low.iloc[k] <= x.low.iloc[k-2:k+3].min():piv.append(k)
    f0=[False]*len(x)
    for a,b in zip(piv,piv[1:]):
        if b-a<=40 and x.low.iloc[b]<x.low.iloc[a] and x.rsi.iloc[b]>x.rsi.iloc[a] and x.rsi.iloc[a]<45 and x.rsi.iloc[b]<50:f0[min(b+2,len(x)-1)]=True
    s0=pd.Series(f0,index=x.index);f1=s0.rolling(7,min_periods=1).max().astype(bool)&(x.rsi>40)&(x.rsi>x.rsi.shift(1))&(x.close>x.close.shift(1));f2=f1.rolling(4,min_periods=1).max().astype(bool)&(x.rsi>48)&(x.ema8>x.ema20)&(x.close>x.ema8)
    out=[]
    for name,sig,hard in [('FUJIMOTO_F2',f2,False),('FUJIMOTO_F4',f2,True)]:
        i=60
        while i<len(x)-2:
            if not bool(sig.iloc[i]):i+=1;continue
            ei=i+1;entry=float(x.open.iloc[ei]);end=min(ei+24,len(x)-1);xi=end
            for k in range(ei+1,end+1):
                if hard:
                    if (x.rsi.iloc[k]<50 and x.rsi.iloc[k-1]>=50) or x.close.iloc[k]<x.ema20.iloc[k]:xi=k;break
                else:
                    if k>=ei+3 and x.close.iloc[k]<x.ema8.iloc[k]:xi=k;break
            out.append((name,{'gross':pct(entry,float(x.close.iloc[xi]))}));i=xi+1
    return out

# -------- Ethan QQQ --------
def ethan_day(cur):
    q=pd.DataFrame(cur,columns=['time','open','high','low','close','volume']);q['bucket']=np.arange(len(q))//5
    z=q.groupby('bucket').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna().astype(float).reset_index(drop=True)
    if len(z)<60:return []
    z['rng']=z.high-z.low;z['body']=(z.close-z.open).abs();out=[];i=50
    while i<len(z)-2:
        prev=z.iloc[i-18:i];med=float(prev.rng.median())
        if not np.isfinite(med) or med<=0:i+=1;continue
        res=float(prev.high.max());sup=float(prev.low.min());pad=.12*med;row=z.iloc[i]
        lb=float(row.close)>res+pad+.05*med;sb=float(row.close)<sup-pad-.05*med
        if not(lb or sb):i+=1;continue
        side='L' if lb else 'S';zlo,zhi=(res-pad,res+pad) if lb else (sup-pad,sup+pad);breakbody=max(float(row.body),1e-12);approach=[];ei=None
        for j in range(i+1,min(len(z),i+9)):
            r=z.iloc[j];touch=float(r.low)<=zhi and float(r.high)>=zlo
            if not touch:
                if (side=='L' and float(r.close)>zhi) or (side=='S' and float(r.close)<zlo):approach.append(j)
                continue
            ab=np.mean([float(z.iloc[k].body) for k in approach[-3:]]) if approach else float(r.body);slow=len(approach)>=2 and ab<=.70*breakbody
            rng=max(float(r.high-r.low),1e-12)
            rej=((min(r.open,r.close)-r.low)/rng>=.35 and r.close>=r.open) if side=='L' else ((r.high-max(r.open,r.close))/rng>=.35 and r.close<=r.open)
            if slow or rej:ei=j
            break
        if ei is None:i+=1;continue
        e=z.iloc[ei];entry=float(e.close);stop=min(float(e.low),zlo-pad) if side=='L' else max(float(e.high),zhi+pad);risk=entry-stop if side=='L' else stop-entry
        if risk<=0:i+=1;continue
        target=entry+1.5*risk if side=='L' else entry-1.5*risk;end=min(len(z)-1,ei+24);xp=float(z.iloc[end].close);xi=end
        for k in range(ei+1,end+1):
            r=z.iloc[k]
            if side=='L':
                if float(r.low)<=stop:xp=stop;xi=k;break
                if float(r.high)>=target:xp=target;xi=k;break
            else:
                if float(r.high)>=stop:xp=stop;xi=k;break
                if float(r.low)<=target:xp=target;xi=k;break
        gross=pct(entry,xp) if side=='L' else pct(xp,entry)
        out.append(('ETHAN_QQQ',{'gross':gross}));i=max(i+1,xi+1)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(DB_DEFAULT));ap.add_argument('--max-days',type=int,default=135);args=ap.parse_args();t0=time.time()
    con=sqlite3.connect(args.db);byfam=defaultdict(list);all_dates=set()
    for sym in SYMS:
        dm=load_days(con,sym,args.max_days);ds=sorted(dm)
        for di,d in enumerate(ds):
            cur=dm[d];all_dates.add(d)
            if di>0:
                w=williams_day(dm[ds[di-1]],cur)
                if w:byfam['WILLIAMS'].append({'date':d,'symbol':sym,**w})
            for fam,t in ma20_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
            for fam,t in fujimoto_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
            if sym=='QQQ':
                for fam,t in ethan_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
        print('LOAD',sym,'DAYS',max(0,len(ds)-1))
    con.close()
    dates=sorted(all_dates);a=int(len(dates)*.60);b=int(len(dates)*.80);spl={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('=== V135 CROSS-FAMILY MASTER TOURNAMENT ===');print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in spl.items()})
    result=[]
    for fam,tr in sorted(byfam.items()):
        print('\n--',fam,'TOTAL_TRADES',len(tr),'--')
        row={'family':fam}
        for cost in COSTS:
            for lab in ('IS','OOS','HOLDOUT'):
                z=metrics([x for x in tr if x['date'] in spl[lab]],cost)
                print('COST',cost,lab,z)
                if cost==0.08:row[lab]=z
        o=row.get('OOS');h=row.get('HOLDOUT');eligible=bool(o and h and o['n']>=15 and h['n']>=15 and o['avg']>0 and h['avg']>0 and o['pf']>1 and h['pf']>1)
        row['eligible']=eligible
        score=(-1e9 if not eligible else h['avg']*.45+(h['pf']-1)*.20+(h['win']/100)*.10+h['mdd']*.01+o['avg']*.20)
        row['score']=score;result.append(row)
    result.sort(key=lambda x:x['score'],reverse=True)
    print('\n=== ROBUST CROSS-FAMILY RANK @8bps ===')
    for i,r in enumerate(result,1):
        o=r.get('OOS');h=r.get('HOLDOUT');print(i,r['family'],'ELIG',r['eligible'],'SCORE',f"{r['score']:.4f}",'OOS',o,'HOLDOUT',h)
    winner=next((r for r in result if r['eligible']),None)
    print('WINNER',winner['family'] if winner else 'NONE')
    print('FINAL_PASS',bool(winner and winner['HOLDOUT']['pf']>=1.3 and winner['OOS']['pf']>=1.3 and winner['HOLDOUT']['avg']>0 and winner['OOS']['avg']>0))
    report={'split':{k:sorted(v) for k,v in spl.items()},'rank':result};Path('/tmp/v135_cross_family_master_tournament.json').write_text(json.dumps(report,indent=2,default=str))
    print('REPORT /tmp/v135_cross_family_master_tournament.json');print('ELAPSED_SEC',round(time.time()-t0,1))
if __name__=='__main__':main()
