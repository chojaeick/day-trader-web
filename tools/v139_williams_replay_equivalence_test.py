#!/usr/bin/env python3
"""V139 historical replay <-> frozen USA module equivalence test.

READ ONLY / NO ORDERS / NO DOWNLOADS.
Runs the V136 frozen Williams logic and the runtime frozen module on the same
historical 1-minute bars. Requires exact agreement on entry/exit decisions.
"""
from __future__ import annotations
import importlib.util, sqlite3, sys
from pathlib import Path
from datetime import datetime

ROOT=Path('/home/ubuntu/day-trader-api')
DB=ROOT/'daytrader.db'
MOD=ROOT/'live_server'/'williams_usa_frozen.py'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']

def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0);out=[float(vals[0])]
    for v in vals[1:]:out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals,p=2):
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
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)];out=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1];m=sum(w)/p;md=sum(abs(x-m) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-m)/(0.015*md)
    return out

def load_days(con,sym,max_days=135):
    ds=[str(r[0]) for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(sym,max_days+1)).fetchall()]
    out={}
    for d in sorted(ds):
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(sym,d)).fetchall()
        if len(rows)>=300:out[d]=rows
    return out

def load_mod():
    spec=importlib.util.spec_from_file_location('williams_usa_frozen',MOD)
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m);return m

def raw_hhmm(raw):
    s=str(raw).strip()
    # et_time in this DB may be HH:MM[:SS], ISO, or compact digits.
    if 'T' in s:
        tail=s.split('T',1)[1]
        if ':' in tail:
            try:return int(tail[:2])*100+int(tail[3:5])
            except Exception:pass
    if ':' in s:
        parts=s.split(':')
        try:return int(parts[0][-2:])*100+int(parts[1][:2])
        except Exception:pass
    d=''.join(ch for ch in s if ch.isdigit())
    if len(d)>=6:return int(d[-6:-2])
    if len(d)>=4:return int(d[-4:])
    return 0

def ts_et_string(raw, trade_date):
    hhmm=raw_hhmm(raw)
    hh,mm=hhmm//100,hhmm%100
    # Explicit ET wall-clock string; frozen module recognizes offset-aware ISO.
    # DST offset is irrelevant for _et_minute because only local wall-clock hour/minute is used.
    return f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T{hh:02d}:{mm:02d}:00-04:00'

def main():
    if not MOD.exists():
        print('MODULE_MISSING',MOD);raise SystemExit(2)
    m=load_mod();con=sqlite3.connect(DB)
    entry_total=entry_match=exit_total=exit_match=0;mismatches=[];trades=0
    for sym in SYMS:
        dm=load_days(con,sym);ds=sorted(dm)
        for di in range(1,len(ds)):
            prev,cur=dm[ds[di-1]],dm[ds[di]]
            H=[float(r[2]) for r in cur];L=[float(r[3]) for r in cur];C=[float(r[4]) for r in cur];V=[float(r[5] or 0) for r in cur]
            ph=max(float(r[2]) for r in prev);pl=min(float(r[3]) for r in prev);op=float(cur[0][1]);trig=op+0.5*(ph-pl)
            r2=rsi(C,2);cc=cci(H,L,C,20);e12=ema(C,12);e26=ema(C,26);mac=[a-b for a,b in zip(e12,e26)];sig=ema(mac,9);hist=[a-b for a,b in zip(mac,sig)]
            seen=False;entry_i=None
            for i in range(20,len(cur)-2):
                cross=C[i-1]<=trig<C[i]
                if not cross or r2[i] is None or r2[i]<=50:continue
                if seen:continue
                seen=True
                prior=V[max(0,i-10):i];va=sum(prior)/len(prior) if prior else 0.0
                raw_ok=(va>0 and V[i]>=1.5*va and cc[i] is not None and cc[i]>100 and hist[i]>hist[i-1])
                hhmm=raw_hhmm(cur[i][0])
                raw_ok=raw_ok and (930<=hhmm<=1100)
                mod=m.entry_signal(ts=ts_et_string(cur[i][0],ds[di]),prev_crossed=False,cross_now=True,rsi2=r2[i],day_open=op,prev_high=ph,prev_low=pl,volume=V[i],prior10_volume_avg=va,cci20=cc[i],macd_hist=hist[i],prev_macd_hist=hist[i-1])
                entry_total+=1
                if bool(mod['signal'])==bool(raw_ok):entry_match+=1
                else:mismatches.append(('ENTRY',sym,ds[di],i,raw_ok,mod,'raw_hhmm',hhmm,'raw_ts',cur[i][0]))
                if raw_ok:entry_i=i
                break
            if entry_i is None:continue
            trades+=1;entry=C[entry_i];weak_raw=0;weak_mod=0
            for j in range(entry_i+1,len(C)):
                pnl=(C[j]/entry-1)*100
                weak=bool(mac[j]<sig[j] and cc[j] is not None and cc[j-1] is not None and cc[j]<cc[j-1])
                weak_raw=weak_raw+1 if weak else 0
                raw_exit=bool(pnl<=-1.0 or weak_raw>=2)
                mod=m.exit_signal(entry_price=entry,price=C[j],macd=mac[j],signal=sig[j],cci20=cc[j],prev_cci20=cc[j-1],weak_run=weak_mod)
                weak_mod=int(mod['weak_run']);exit_total+=1
                if bool(mod['exit'])==raw_exit:exit_match+=1
                else:mismatches.append(('EXIT',sym,ds[di],j,raw_exit,mod))
                if raw_exit:break
    con.close()
    print('=== V139 WILLIAMS REPLAY EQUIVALENCE TEST ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    print('TRADES_TESTED=',trades)
    print('ENTRY_DECISIONS=',entry_total,'MATCH=',entry_match,'RATE=',round(100*entry_match/entry_total,3) if entry_total else 100.0)
    print('EXIT_DECISIONS=',exit_total,'MATCH=',exit_match,'RATE=',round(100*exit_match/exit_total,3) if exit_total else 100.0)
    print('MISMATCHES=',len(mismatches))
    for x in mismatches[:20]:print('MISMATCH',x)
    ok=(len(mismatches)==0 and entry_total>0 and exit_total>0)
    print('EQUIVALENCE_PASS=',ok)
    print('NEXT=' + ('WIRE_ISOLATED_FROZEN_MODULE_TO_USA_PAPER_PATH' if ok else 'FIX_EQUIVALENCE_MISMATCH_ONLY; DO_NOT_DEPLOY'))

if __name__=='__main__':main()
