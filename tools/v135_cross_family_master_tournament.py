#!/usr/bin/env python3
"""V135 cross-family master tournament - stdlib-only hotfix.
READ ONLY / NO ORDERS / NO DOWNLOADS.

Compares representative strategy families on one fixed US master DB using
chronological 60/20/20 split and common friction assumptions.

Families:
- WILLIAMS: V5 strict + COMBO2 + 1% hard stop
- MA20_GAP: gap reversion
- MA20_PULLBACK: trend pullback
- FUJIMOTO_F2/F4: causal staged reimplementation
- ETHAN_QQQ: source-constrained breakout/retest proxy

No numpy/pandas dependency.
"""
from __future__ import annotations
import argparse, math, sqlite3, statistics, time
from pathlib import Path
from collections import defaultdict

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

def sma(vals,n):
    out=[None]*len(vals);s=0.0
    for i,v in enumerate(vals):
        s+=v
        if i>=n:s-=vals[i-n]
        if i>=n-1:out[i]=s/n
    return out

def resample5(cur):
    out=[]
    for i in range(0,len(cur),5):
        g=cur[i:i+5]
        if len(g)<5:continue
        out.append({'time':g[-1][0],'open':float(g[0][1]),'high':max(float(x[2]) for x in g),'low':min(float(x[3]) for x in g),'close':float(g[-1][4]),'volume':sum(float(x[5] or 0) for x in g)})
    return out

# -------- Williams --------
def williams_day(prev,cur):
    if len(prev)<100 or len(cur)<40:return None
    H=[float(r[2]) for r in cur];C=[float(r[4]) for r in cur];V=[float(r[5] or 0) for r in cur]
    L=[float(r[3]) for r in cur];ph=max(float(r[2]) for r in prev);pl=min(float(r[3]) for r in prev);trig=float(cur[0][1])+0.5*(ph-pl)
    r2=rsi(C,2);cc=cci(H,L,C,20);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9)
    first=False
    for i in range(20,len(cur)-2):
        if not (C[i-1]<=trig<C[i]) or r2[i] is None or r2[i]<=50:continue
        if first:continue
        first=True;t=hhmm(cur[i][0]);prior=V[max(0,i-10):i];va=sum(prior)/len(prior) if prior else 0
        if t is None or not(930<=t<=1100) or va<=0 or V[i]<1.5*va or cc[i] is None or cc[i]<=100 or mac[i]-sig[i]<=mac[i-1]-sig[i-1]:return None
        weak=0;entry=C[i];ix=len(C)-1
        for j in range(i+1,len(C)):
            if pct(entry,C[j])<=-1.0:ix=j;break
            cd=cc[j] is not None and cc[j-1] is not None and cc[j]<cc[j-1];combo=mac[j]<sig[j] and cd;weak=weak+1 if combo else 0
            if weak>=2:ix=j;break
        return {'gross':pct(entry,C[ix])}
    return None

# -------- MA20 --------
def ma20_day(cur):
    O=[float(r[1]) for r in cur];H=[float(r[2]) for r in cur];L=[float(r[3]) for r in cur];C=[float(r[4]) for r in cur]
    M=sma(C,20);out=[]
    i=21
    while i<len(C)-1:
        if M[i] is None or M[i-1] is None:i+=1;continue
        gap=(M[i]-C[i])/M[i]*100;pg=(M[i-1]-C[i-1])/M[i-1]*100;px=C[i];target=px+(M[i]-px)*0.25
        if gap>=2.0 and gap<pg and pct(px,target)>=0.50:
            end=min(len(C)-1,i+30);entrygap=gap;xi=end;xp=C[end]
            for j in range(i+1,end+1):
                gapj=(M[j]-C[j])/M[j]*100 if M[j] else 0
                if H[j]>=target:xi=j;xp=target;break
                if gapj>=entrygap+0.75:xi=j;xp=C[j];break
            out.append(('MA20_GAP',{'gross':pct(px,xp)}));i=xi+1
        else:i+=1
    i=22
    while i<len(C)-1:
        if M[i] is None or i<11:i+=1;continue
        slope=M[i]-M[i-3] if M[i-3] is not None else 0;low1=min(L[i-10:i-5]);low2=min(L[i-5:i]);px=C[i]
        cond=slope>0 and low2>low1 and abs(px/M[i]-1)*100<=0.75 and C[i]>O[i] and C[i]>C[i-1]
        if not cond:i+=1;continue
        target=max(H[max(0,i-10):i]);stop=low2
        if target<=px or stop>=px:i+=1;continue
        end=min(len(C)-1,i+30);xi=end;xp=C[end]
        for j in range(i+1,end+1):
            if L[j]<=stop:xi=j;xp=stop;break
            if H[j]>=target:xi=j;xp=target;break
        out.append(('MA20_PULLBACK',{'gross':pct(px,xp)}));i=xi+1
    return out

# -------- Fujimoto --------
def fujimoto_day(cur):
    x=resample5(cur)
    if len(x)<70:return []
    C=[r['close'] for r in x];L=[r['low'] for r in x];O=[r['open'] for r in x]
    R=rsi(C,14);E8=ema(C,8);E20=ema(C,20)
    piv=[]
    for k in range(2,len(x)-2):
        if L[k]<=min(L[k-2:k+3]):piv.append(k)
    f0=[False]*len(x)
    for a,b in zip(piv,piv[1:]):
        if b-a<=40 and R[a] is not None and R[b] is not None and L[b]<L[a] and R[b]>R[a] and R[a]<45 and R[b]<50:f0[min(b+2,len(x)-1)]=True
    f1=[False]*len(x);f2=[False]*len(x)
    for i in range(len(x)):
        recent=any(f0[max(0,i-6):i+1])
        if recent and R[i] is not None and i>0 and R[i]>40 and R[i-1] is not None and R[i]>R[i-1] and C[i]>C[i-1]:f1[i]=True
        recent1=any(f1[max(0,i-3):i+1])
        if recent1 and R[i] is not None and R[i]>48 and E8[i]>E20[i] and C[i]>E8[i]:f2[i]=True
    out=[]
    for name,hard in [('FUJIMOTO_F2',False),('FUJIMOTO_F4',True)]:
        i=60
        while i<len(x)-2:
            if not f2[i]:i+=1;continue
            ei=i+1;entry=O[ei];end=min(ei+24,len(x)-1);xi=end
            for k in range(ei+1,end+1):
                if hard:
                    if (R[k] is not None and R[k-1] is not None and R[k]<50<=R[k-1]) or C[k]<E20[k]:xi=k;break
                elif k>=ei+3 and C[k]<E8[k]:xi=k;break
            out.append((name,{'gross':pct(entry,C[xi])}));i=xi+1
    return out

# -------- Ethan QQQ --------
def ethan_day(cur):
    z=resample5(cur)
    if len(z)<60:return []
    out=[];i=50
    while i<len(z)-2:
        prev=z[i-18:i];ranges=[r['high']-r['low'] for r in prev];med=statistics.median(ranges) if ranges else 0
        if not math.isfinite(med) or med<=0:i+=1;continue
        res=max(r['high'] for r in prev);sup=min(r['low'] for r in prev);pad=.12*med;r=z[i]
        lb=r['close']>res+pad+.05*med;sb=r['close']<sup-pad-.05*med
        if not(lb or sb):i+=1;continue
        side='L' if lb else 'S';zlo,zhi=(res-pad,res+pad) if lb else (sup-pad,sup+pad);breakbody=max(abs(r['close']-r['open']),1e-12);approach=[];ei=None
        for j in range(i+1,min(len(z),i+9)):
            q=z[j];touch=q['low']<=zhi and q['high']>=zlo
            if not touch:
                if (side=='L' and q['close']>zhi) or (side=='S' and q['close']<zlo):approach.append(j)
                continue
            bodies=[abs(z[k]['close']-z[k]['open']) for k in approach[-3:]];ab=statistics.fmean(bodies) if bodies else abs(q['close']-q['open']);slow=len(approach)>=2 and ab<=.70*breakbody
            rng=max(q['high']-q['low'],1e-12)
            rej=((min(q['open'],q['close'])-q['low'])/rng>=.35 and q['close']>=q['open']) if side=='L' else ((q['high']-max(q['open'],q['close']))/rng>=.35 and q['close']<=q['open'])
            if slow or rej:ei=j
            break
        if ei is None:i+=1;continue
        e=z[ei];entry=e['close'];stop=min(e['low'],zlo-pad) if side=='L' else max(e['high'],zhi+pad);risk=entry-stop if side=='L' else stop-entry
        if risk<=0:i+=1;continue
        target=entry+1.5*risk if side=='L' else entry-1.5*risk;end=min(len(z)-1,ei+24);xp=z[end]['close'];xi=end
        for k in range(ei+1,end+1):
            q=z[k]
            if side=='L':
                if q['low']<=stop:xp=stop;xi=k;break
                if q['high']>=target:xp=target;xi=k;break
            else:
                if q['high']>=stop:xp=stop;xi=k;break
                if q['low']<=target:xp=target;xi=k;break
        out.append(('ETHAN_QQQ',{'gross':pct(entry,xp) if side=='L' else pct(xp,entry)}));i=max(i+1,xi+1)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(DB_DEFAULT));ap.add_argument('--max-days',type=int,default=135);args=ap.parse_args();t0=time.time()
    con=sqlite3.connect(args.db);byfam=defaultdict(list);all_dates=set()
    for sym in SYMS:
        dm=load_days(con,sym,args.max_days);ds=sorted(dm)
        print('LOAD',sym,'DAYS=',max(0,len(ds)-1))
        for di,d in enumerate(ds):
            cur=dm[d];all_dates.add(d)
            if di>0:
                w=williams_day(dm[ds[di-1]],cur)
                if w:byfam['WILLIAMS'].append({'date':d,'symbol':sym,**w})
            for fam,t in ma20_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
            for fam,t in fujimoto_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
            if sym=='QQQ':
                for fam,t in ethan_day(cur):byfam[fam].append({'date':d,'symbol':sym,**t})
    con.close()
    dates=sorted(all_dates);a=int(len(dates)*.60);b=int(len(dates)*.80);split={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('\n=== V135 CROSS-FAMILY MASTER TOURNAMENT ===')
    print('STDLIB_ONLY=YES READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in split.items()})
    rank=[]
    for fam in sorted(byfam):
        print('\n--',fam,'--')
        for cost in COSTS:
            zs={lab:metrics([t for t in byfam[fam] if t['date'] in ds],cost) for lab,ds in split.items()}
            print('COST',cost,'IS',zs['IS'],'OOS',zs['OOS'],'HOLDOUT',zs['HOLDOUT'])
        o=metrics([t for t in byfam[fam] if t['date'] in split['OOS']],0.08);h=metrics([t for t in byfam[fam] if t['date'] in split['HOLDOUT']],0.08)
        eligible=bool(o and h and o['n']>=10 and h['n']>=10 and o['avg']>0 and h['avg']>0 and o['pf']>1 and h['pf']>1)
        score=(min(o['pf'],h['pf'])+min(o['avg'],h['avg'])*2+min(o['win'],h['win'])/100) if eligible else -1e9
        rank.append((score,fam,eligible,o,h))
    rank.sort(reverse=True,key=lambda x:x[0])
    print('\n=== ROBUST CROSS-FAMILY RANK ===')
    for i,(sc,fam,el,o,h) in enumerate(rank,1):print(i,fam,'ELIG',el,'SCORE',f'{sc:.4f}','OOS',o,'HOLDOUT',h)
    winner=next((x for x in rank if x[2]),None)
    if winner:
        _,fam,_,o,h=winner;print('WINNER',fam);print('FINAL_PASS=',bool(o['pf']>=1.3 and h['pf']>=1.3 and o['avg']>0 and h['avg']>0))
    else:
        print('WINNER NONE');print('FINAL_PASS= False')
    print('ELAPSED_SEC',round(time.time()-t0,1))

if __name__=='__main__':main()
