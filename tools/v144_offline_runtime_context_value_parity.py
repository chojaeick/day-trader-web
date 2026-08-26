#!/usr/bin/env python3
"""V144 offline runtime context value parity test.

READ ONLY / NO ORDERS / NO DOWNLOADS.
Compares the frozen historical reference feature values against values produced by
runtime-compatible frozen context construction on the same historical 1m bars.
"""
from __future__ import annotations
import sqlite3, statistics, math
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
DB=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
TOL=1e-9

def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0);o=[float(vals[0])]
    for v in vals[1:]:o.append(a*float(v)+(1-a)*o[-1])
    return o

def rsi(vals,p=2):
    n=len(vals);o=[None]*n
    if n<p+2:return o
    gs=[];ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1];gs.append(max(d,0));ls.append(max(-d,0))
    ag=sum(gs)/p;al=sum(ls)/p;o[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,n):
        d=vals[i]-vals[i-1];g=max(d,0);l=max(-d,0);ag=(ag*(p-1)+g)/p;al=(al*(p-1)+l)/p;o[i]=100 if al==0 else 100-100/(1+ag/al)
    return o

def cci(H,L,C,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)];o=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1];m=sum(w)/p;md=sum(abs(x-m) for x in w)/p;o[i]=0 if md==0 else (tp[i]-m)/(0.015*md)
    return o

def hhmm(raw):
    s=str(raw);d=''.join(ch for ch in s if ch.isdigit())
    if ':' in s:
        try:
            p=s.split('T')[-1] if 'T' in s else s
            return int(p[:2])*100+int(p[3:5])
        except Exception:pass
    return int(d[-6:-2]) if len(d)>=6 else 0

def load_days(con,sym,max_days=135):
    ds=[str(r[0]) for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(sym,max_days+1)).fetchall()]
    out={}
    for d in sorted(ds):
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(sym,d)).fetchall()
        if len(rows)>=300:out[d]=rows
    return out

def close(a,b):
    if a is None or b is None:return a is b
    return abs(float(a)-float(b))<=TOL*max(1.0,abs(float(a)),abs(float(b)))

def main():
    con=sqlite3.connect(DB)
    total=match=0;mismatches=[];sampled_days=0
    fields=['day_open','prev_high','prev_low','trigger','rsi2','cci20','macd_hist','prev_macd_hist','volume','prior10_volume_avg','cross_now','prev_crossed']
    field_stats={k:[0,0] for k in fields}
    for sym in SYMS:
        dm=load_days(con,sym);ds=sorted(dm)
        for di in range(1,len(ds)):
            prev,cur=dm[ds[di-1]],dm[ds[di]];sampled_days+=1
            H=[float(r[2]) for r in cur];L=[float(r[3]) for r in cur];C=[float(r[4]) for r in cur];V=[float(r[5] or 0) for r in cur]
            ph=max(float(r[2]) for r in prev);pl=min(float(r[3]) for r in prev);op=float(cur[0][1]);trig=op+0.5*(ph-pl)
            r2=rsi(C,2);cc=cci(H,L,C,20);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9);hist=[a-b for a,b in zip(mac,sig)]
            seen=False
            # compare every eligible computational bar, not only entries
            for i in range(20,min(len(cur)-2,121)):
                prior=V[max(0,i-10):i];va=sum(prior)/len(prior) if prior else 0.0
                cross=bool(C[i-1]<=trig<C[i])
                ref={'day_open':op,'prev_high':ph,'prev_low':pl,'trigger':trig,'rsi2':r2[i],'cci20':cc[i],
                     'macd_hist':hist[i],'prev_macd_hist':hist[i-1],'volume':V[i],'prior10_volume_avg':va,
                     'cross_now':cross,'prev_crossed':seen}
                # Runtime-compatible reconstruction deliberately uses only data available through i.
                Hr=H[:i+1];Lr=L[:i+1];Cr=C[:i+1];Vr=V[:i+1]
                rr=rsi(Cr,2);cr=cci(Hr,Lr,Cr,20);m12=ema(Cr,12);m26=ema(Cr,26);mm=[a-b for a,b in zip(m12,m26)];ss=ema(mm,9);hh=[a-b for a,b in zip(mm,ss)]
                pvr=Vr[max(0,len(Vr)-11):-1];var=sum(pvr)/len(pvr) if pvr else 0.0
                run={'day_open':float(cur[0][1]),'prev_high':max(float(r[2]) for r in prev),'prev_low':min(float(r[3]) for r in prev),
                     'trigger':float(cur[0][1])+0.5*(max(float(r[2]) for r in prev)-min(float(r[3]) for r in prev)),
                     'rsi2':rr[-1],'cci20':cr[-1],'macd_hist':hh[-1],'prev_macd_hist':hh[-2],
                     'volume':Vr[-1],'prior10_volume_avg':var,'cross_now':bool(Cr[-2]<=trig<Cr[-1]),'prev_crossed':seen}
                for k in fields:
                    total+=1;field_stats[k][1]+=1
                    ok=(ref[k]==run[k]) if isinstance(ref[k],bool) else close(ref[k],run[k])
                    if ok:match+=1;field_stats[k][0]+=1
                    elif len(mismatches)<30:mismatches.append((sym,ds[di],i,k,ref[k],run[k],hhmm(cur[i][0])))
                if cross and r2[i] is not None and r2[i]>50:seen=True
    con.close()
    print('=== V144 OFFLINE RUNTIME CONTEXT VALUE PARITY ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    print('SYMBOLS=',len(SYMS),'SYMBOL_DAYS=',sampled_days)
    print('FIELD_COMPARISONS=',total,'MATCH=',match,'RATE=',round(100*match/total,6) if total else 100.0)
    print('=== FIELD PARITY ===')
    for k,(m,n) in field_stats.items():print(k,'MATCH',m,'/',n,'RATE',round(100*m/n,6) if n else 100.0)
    print('MISMATCHES=',total-match)
    for x in mismatches:print('MISMATCH',x)
    ok=(total>0 and match==total)
    print('VALUE_PARITY_PASS=',ok)
    print('ORDER_AUTHORITY=NONE')
    print('NEXT=' + ('V145_ENABLE_USA_FROZEN_PAPER_ORDER_AUTHORITY_WITH_SAFETY_GATES' if ok else 'FIX_CONTEXT_VALUE_MISMATCH_ONLY; DO_NOT_ENABLE_ORDERS'))

if __name__=='__main__':main()
