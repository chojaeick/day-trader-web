#!/usr/bin/env python3
"""V130 Williams causal regime/trend-gate tournament.

READ ONLY. NO API. NO ORDERS. NO DOWNLOADS.

Purpose:
- Test the user's hypothesis from live KR paper trading: entry quality may be acceptable,
  but taking otherwise-valid entries against a weak symbol/market trend hurts results.
- Keep exit families simple and previously studied; do NOT add a 5-minute forced hold.
- For losing trades, test a STRUCT_FAIL exit that needs multiple simultaneous failures
  (below VWAP + falling MACD hist + EMA9<EMA20 for 2 bars), not one weak indicator.
- For profitable trades, LOCKTRAIL allows pullbacks and only tightens after profit exists.
- Chronological IS/OOS/HOLDOUT split. No tuning on HOLDOUT.
"""
from __future__ import annotations
import argparse, sqlite3, statistics, time
from collections import defaultdict, Counter

ROOT='/home/ubuntu/day-trader-api'
DB_DEFAULT=ROOT+'/daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
INVERSE={'SOXS','SQQQ'}
COSTS=(8.0,12.0,16.0)


def ema(v,span):
    if not v:return []
    a=2/(span+1); o=[float(v[0])]
    for x in v[1:]:o.append(a*float(x)+(1-a)*o[-1])
    return o

def rsi(v,p):
    out=[None]*len(v)
    if len(v)<p+2:return out
    g=[];l=[]
    for i in range(1,p+1):
        d=v[i]-v[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/p;al=sum(l)/p;out[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,len(v)):
        d=v[i]-v[i-1];gg=max(d,0);ll=max(-d,0);ag=(ag*(p-1)+gg)/p;al=(al*(p-1)+ll)/p
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def cci(H,L,C,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)];o=[None]*len(C)
    for i in range(p-1,len(C)):
        w=tp[i-p+1:i+1];m=sum(w)/p;md=sum(abs(x-m) for x in w)/p;o[i]=0 if md==0 else (tp[i]-m)/(0.015*md)
    return o

def hhmm(x):
    s=str(x)
    if 'T' in s:s=s.split('T',1)[1]
    if ':' in s:
        a=s[:5].split(':');return int(a[0])*100+int(a[1])
    d=''.join(z for z in s if z.isdigit())
    return int(d[-6:-2]) if len(d)>=6 else None

def pct(a,b):return (b/a-1)*100 if a else 0.0

def load(con,s,maxd):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(s,maxd+1))]
    out={}
    for d in sorted(ds):
        r=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(r)>=300:out[str(d)]=r
    return out

def arr(rows):
    H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows];V=[float(x[5] or 0) for x in rows]
    r2=rsi(C,2);c20=cci(H,L,C);e9=ema(C,9);e20=ema(C,20);e12=ema(C,12);e26=ema(C,26);m=[a-b for a,b in zip(e12,e26)];sg=ema(m,9);hist=[a-b for a,b in zip(m,sg)]
    vw=[];pv=vv=0.0
    for h,l,c,v in zip(H,L,C,V):
        pv+=((h+l+c)/3)*v;vv+=v;vw.append(pv/vv if vv else c)
    return {'H':H,'L':L,'C':C,'V':V,'r2':r2,'cci':c20,'e9':e9,'e20':e20,'macd':m,'sig':sg,'hist':hist,'vwap':vw,'rows':rows}

def raw_first(prev,cur):
    if len(prev)<100 or len(cur)<70:return None
    A=arr(cur);ph=max(float(x[2]) for x in prev);pl=min(float(x[3]) for x in prev);op=float(cur[0][1]);tr=op+0.5*(ph-pl)
    C=A['C'];V=A['V']
    for i in range(20,len(C)-10):
        if not(C[i-1]<=tr<C[i]):continue
        if A['r2'][i] is None or A['r2'][i]<=50:continue
        pr=V[max(0,i-10):i];va=sum(pr)/len(pr) if pr else 0;vr=V[i]/va if va else 0;t=hhmm(cur[i][0])
        return A|{'arrow_i':i,'trigger':tr,'vr':vr,'hhmm':t}
    return None

def entries(prev,cur):
    a=raw_first(prev,cur);o={}
    if not a:return o
    i=a['arrow_i'];C=a['C'];hist=a['hist'];vw=a['vwap'];cc=a['cci'];t=a['hhmm']
    if t is not None and 930<=t<=1100 and a['vr']>=1.5 and cc[i] is not None and cc[i]>100 and i>=1 and hist[i]>hist[i-1]:
        o['V5_STRICT']=a|{'entry_i':i,'entry':C[i]}
    if i+5<len(C):
        j=i+5;r=pct(C[i],C[j]);ha=hist[j]>hist[i];vh=all(C[k]>=vw[k] for k in range(i+1,j+1))
        if r>=0.20 and ha and vh:o['T5_02_HIST_VWAP']=a|{'entry_i':j,'entry':C[j]}
        if r>=0.30 and ha and vh:o['T5_03_HIST_VWAP']=a|{'entry_i':j,'entry':C[j]}
    return o

def state(A,i,direction=1):
    if i<5 or i>=len(A['C']):return {'basic':False,'strong':False}
    C=A['C'];e9=A['e9'];e20=A['e20'];h=A['hist'];vw=A['vwap']
    if direction>0:
        basic=C[i]>=vw[i] and e9[i]>=e20[i] and h[i]>=0
        strong=basic and e20[i]>e20[i-5] and C[i]>C[i-3]
    else:
        basic=C[i]<=vw[i] and e9[i]<=e20[i] and h[i]<=0
        strong=basic and e20[i]<e20[i-5] and C[i]<C[i-3]
    return {'basic':basic,'strong':strong}

def gate_ok(name,sym,e,bench):
    i=e['entry_i'];inv=sym in INVERSE;sd=-1 if inv else 1
    ss=state(e,i,1)  # inverse ETF itself still needs to be trending UP when bought long
    q=bench.get('QQQ');p=bench.get('SPY')
    qs=state(q,i,sd) if q and i<len(q['C']) else {'basic':False,'strong':False}
    ps=state(p,i,sd) if p and i<len(p['C']) else {'basic':False,'strong':False}
    if name=='NONE':return True
    if name=='SYM':return ss['basic']
    if name=='SYM_STRONG':return ss['strong']
    if name=='QQQ':return qs['basic']
    if name=='QQQ_SPY':return qs['basic'] and ps['basic']
    if name=='SYM_QQQ':return ss['basic'] and qs['basic']
    if name=='SYM_QQQ_SPY':return ss['basic'] and qs['basic'] and ps['basic']
    if name=='SYMSTR_QQQ':return ss['strong'] and qs['basic']
    if name=='SYMSTR_QQQ_SPY':return ss['strong'] and qs['basic'] and ps['basic']
    return False

def exit_trade(e,mode,stop):
    i0=e['entry_i'];en=e['entry'];C=e['C'];H=e['H'];L=e['L'];vw=e['vwap'];h=e['hist'];e9=e['e9'];e20=e['e20'];cc=e['cci'];m=e['macd'];sg=e['sig']
    peak=en;weak=0;struct=0;ix=len(C)-1;reason='EOD'
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]);pr=pct(en,peak);cr=pct(en,C[i]);dd=pct(peak,C[i])
        if cr<=-stop:ix=i;reason='HARD';break
        cd=cc[i] is not None and cc[i-1] is not None and cc[i]<cc[i-1];combo=m[i]<sg[i] and cd
        weak=weak+1 if combo else 0
        # Stronger early failure: 2 consecutive bars with price below VWAP, negative/falling hist, EMA trend broken.
        fail=(C[i]<vw[i] and h[i]<h[i-1] and h[i]<0 and e9[i]<e20[i])
        struct=struct+1 if fail else 0
        if mode=='COMBO2':
            if weak>=2:ix=i;reason='COMBO2';break
        elif mode in ('LOCKTRAIL','STRUCT_LOCKTRAIL'):
            if mode=='STRUCT_LOCKTRAIL' and pr<0.30 and struct>=2:ix=i;reason='STRUCT_FAIL2';break
            if pr>=0.30 and cr<=0.00:ix=i;reason='LOCK03_BE';break
            if pr>=0.50 and cr<=0.20:ix=i;reason='LOCK05_02';break
            if pr>=0.80 and dd<=-0.30:ix=i;reason='TRAIL08_03';break
            if pr>=1.20 and dd<=-0.40:ix=i;reason='TRAIL12_04';break
            if pr>=2.00 and dd<=-0.55:ix=i;reason='TRAIL20_055';break
    r=pct(en,C[ix]);mfe=pct(en,max(H[i0:ix+1]));mae=pct(en,min(L[i0:ix+1]));return {'ret':r,'mfe':mfe,'mae':mae,'gb':max(0,mfe-r),'reason':reason,'sym':e['sym'],'date':e['date']}

def met(ts,costbps):
    if not ts:return None
    cp=costbps/100;rs=[x['ret']-cp for x in ts];w=[x for x in rs if x>0];l=[x for x in rs if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl else (999 if gp else 0)
    eq=pk=0;mdd=0
    for x in rs:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return {'n':len(rs),'win':100*len(w)/len(rs),'avg':statistics.fmean(rs),'pf':pf,'net':sum(rs),'mdd':mdd,'mfe':statistics.fmean(x['mfe'] for x in ts),'mae':statistics.fmean(x['mae'] for x in ts),'gb':statistics.fmean(x['gb'] for x in ts)}
def eligible(z):return bool(z and z['n']>=15 and z['avg']>0 and z['pf']>1.0)
def score(z):return -1e9 if not z else z['avg']*.45+(z['pf']-1)*.18+z['win']/100*.12+z['mdd']*.012-z['gb']*.05

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=DB_DEFAULT);ap.add_argument('--max-days',type=int,default=135);a=ap.parse_args();t0=time.time()
    con=sqlite3.connect(a.db);dm={s:load(con,s,a.max_days) for s in SYMS};con.close(); cache={}
    for s in SYMS:
        for d,r in dm[s].items():cache[(s,d)]=arr(r)
    events=[]
    for s in SYMS:
        ds=sorted(dm[s]);n=0
        for k in range(1,len(ds)):
            d=ds[k];es=entries(dm[s][ds[k-1]],dm[s][d]);bench={b:cache.get((b,d)) for b in ('QQQ','SPY')}
            for en,e in es.items():e=e|{'sym':s,'date':d};events.append((d,s,en,e,bench));n+=1
        print('LOAD',s,'DAYS=',max(0,len(ds)-1),'ENTRIES=',n)
    dates=sorted(set(x[0] for x in events));n=len(dates);p1=int(n*.60);p2=int(n*.80);spl={'IS':set(dates[:p1]),'OOS':set(dates[p1:p2]),'HOLDOUT':set(dates[p2:])}
    print('\n=== V130 WILLIAMS REGIME/TREND GATE TOURNAMENT ===');print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE');print('DATES',n,'SPLIT',{k:len(v) for k,v in spl.items()})
    ens=['V5_STRICT','T5_02_HIST_VWAP','T5_03_HIST_VWAP'];gates=['NONE','SYM','SYM_STRONG','QQQ','QQQ_SPY','SYM_QQQ','SYM_QQQ_SPY','SYMSTR_QQQ','SYMSTR_QQQ_SPY'];exs=['COMBO2','LOCKTRAIL','STRUCT_LOCKTRAIL'];stops=[1.0,1.25,1.5]
    book=defaultdict(lambda:defaultdict(list))
    for d,s,en,e,b in events:
        lab='IS' if d in spl['IS'] else ('OOS' if d in spl['OOS'] else 'HOLDOUT')
        for g in gates:
            if not gate_ok(g,s,e,b):continue
            for ex in exs:
                for st in stops:book[(en,g,ex,st)][lab].append(exit_trade(e,ex,st))
    rows=[]
    for key,z in book.items():
        O=met(z['OOS'],8);H=met(z['HOLDOUT'],8);ok=eligible(O);sc=score(H) if ok else -1e9;rows.append((sc,key,O,H,z))
    rows.sort(reverse=True,key=lambda x:x[0]);print('\n=== OOS-ELIGIBLE HOLDOUT RANK @8bps ===')
    for r,(sc,key,O,H,z) in enumerate(rows[:30],1):
        if not O or not H:continue
        print(r,key,'ELIG',eligible(O),'O_N',O['n'],'O_WIN',f"{O['win']:.2f}",'O_AVG',f"{O['avg']:.4f}",'O_PF',f"{O['pf']:.3f}",'H_N',H['n'],'H_WIN',f"{H['win']:.2f}",'H_AVG',f"{H['avg']:.4f}",'H_PF',f"{H['pf']:.3f}",'H_NET',f"{H['net']:.2f}",'H_MDD',f"{H['mdd']:.2f}",'H_GB',f"{H['gb']:.3f}")
    elig=[x for x in rows if x[0]>-1e8]
    if not elig:print('NO_ELIGIBLE');return
    _,win,O,H,z=elig[0];print('\nWINNER',win)
    print('=== COST STRESS ===')
    passall=True
    for cb in COSTS:
        oo=met(z['OOS'],cb);hh=met(z['HOLDOUT'],cb);print('COST',cb,'OOS',oo,'HOLDOUT',hh)
        if cb<=12 and not(oo and hh and oo['n']>=15 and hh['n']>=15 and oo['avg']>0 and hh['avg']>0 and oo['pf']>=1.20 and hh['pf']>=1.20):passall=False
    print('=== HOLDOUT BY SYMBOL ===')
    for s in SYMS:
        q=[x for x in z['HOLDOUT'] if x['sym']==s]
        if q:print(s,met(q,8))
    print('EXIT_REASONS',dict(Counter(x['reason'] for x in z['HOLDOUT'])))
    print('FINAL_PASS=',passall)
    print('ELAPSED_SEC',round(time.time()-t0,1))

if __name__=='__main__':main()
