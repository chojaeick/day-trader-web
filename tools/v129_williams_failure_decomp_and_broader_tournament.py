#!/usr/bin/env python3
"""V129 Williams failure decomposition + broader candidate tournament.
READ ONLY. NO API. NO ORDERS. NO DOWNLOADS.

Goals:
1) Explain why V128 had NO_ELIGIBLE_CANDIDATE instead of blindly tuning thresholds.
2) Re-run a broader but still finite Williams candidate set using the same master DB.
3) Separate entry-quality failure from exit-quality failure.
4) Produce OOS/HOLDOUT metrics plus per-symbol contribution and exit-reason diagnostics.
"""
from __future__ import annotations
import argparse, sqlite3, statistics, math, json, csv, time
from collections import defaultdict
from pathlib import Path

ROOT=Path('/home/ubuntu/day-trader-api')
DEFAULT_DB=ROOT/'daytrader.db'
SYMS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']


def pct(a,b): return (b/a-1)*100 if a else 0.0

def ema(vals,span):
    if not vals:return []
    a=2/(span+1); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals,p):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    g=[];l=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1];g.append(max(d,0));l.append(max(-d,0))
    ag=sum(g)/p;al=sum(l)/p;out[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,len(vals)):
        d=vals[i]-vals[i-1];gg=max(d,0);ll=max(-d,0)
        ag=(ag*(p-1)+gg)/p;al=(al*(p-1)+ll)/p;out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def cci(H,L,C,p=20):
    tp=[(h+l+c)/3 for h,l,c in zip(H,L,C)];out=[None]*len(tp)
    for i in range(p-1,len(tp)):
        w=tp[i-p+1:i+1];ma=sum(w)/p;md=sum(abs(x-ma) for x in w)/p
        out[i]=0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(s,max_days+1))]
    ds=sorted(ds);out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if len(rows)>=300: out[str(d)]=rows
    return out

def arr(rows):
    H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows];V=[float(x[5] or 0) for x in rows]
    r2=rsi(C,2);c20=cci(H,L,C);e12=ema(C,12);e26=ema(C,26);macd=[a-b for a,b in zip(e12,e26)];sig=ema(macd,9);hist=[a-b for a,b in zip(macd,sig)]
    vw=[];pv=vv=0
    for h,l,c,v in zip(H,L,C,V):
        tp=(h+l+c)/3;pv+=tp*v;vv+=v;vw.append(pv/vv if vv else c)
    return H,L,C,V,r2,c20,macd,sig,hist,vw

def raw_first(prev,cur):
    if len(prev)<100 or len(cur)<70:return None
    ph=max(float(x[2]) for x in prev);pl=min(float(x[3]) for x in prev);op=float(cur[0][1]);trig=op+0.5*(ph-pl)
    H,L,C,V,r2,c20,macd,sig,hist,vw=arr(cur)
    for i in range(20,len(cur)-15):
        if C[i-1]<=trig<C[i] and r2[i] is not None and r2[i]>50:
            prior=V[max(0,i-10):i];vavg=sum(prior)/len(prior) if prior else 0;vr=V[i]/vavg if vavg else 0
            return {'i0':i,'H':H,'L':L,'C':C,'V':V,'r2':r2,'cci':c20,'macd':macd,'sig':sig,'hist':hist,'vwap':vw,'vr':vr,'trig':trig}
    return None

def entries(prev,cur):
    e=raw_first(prev,cur)
    if not e:return {}
    C=e['C'];H=e['H'];L=e['L'];V=e['V'];hist=e['hist'];vw=e['vwap'];i=e['i0'];out={}
    # broader but finite existing-style families
    if e['vr']>=1.5 and e['cci'][i] is not None and e['cci'][i]>100 and i>=1 and hist[i]>hist[i-1]:
        out['V5_STRICT']=(i,C[i])
    for w,retmin in [(3,0.0),(3,0.10),(5,0.10),(5,0.20),(5,0.30)]:
        j=i+w
        if j>=len(C):continue
        rr=pct(C[i],C[j]);hacc=hist[j]>hist[i];vwh=all(C[k]>=vw[k] for k in range(i+1,j+1))
        tag=f'T{w}_{str(retmin).replace(".","")}';
        if rr>=retmin and hacc: out[tag+'_HIST']=(j,C[j])
        if rr>=retmin and hacc and vwh: out[tag+'_HIST_VWAP']=(j,C[j])
    return {k:{**e,'entry_i':j,'entry':p} for k,(j,p) in out.items()}

def do_exit(e,mode,hard=1.5):
    i0=e['entry_i'];entry=e['entry'];H=e['H'];L=e['L'];C=e['C'];cci=e['cci'];macd=e['macd'];sig=e['sig'];vw=e['vwap']
    peak=entry;weakrun=0;belowvw=0
    reason='EOD';ix=len(C)-1
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]);pr=pct(entry,peak);cr=pct(entry,C[i]);dd=pct(peak,C[i])
        cdown=cci[i] is not None and cci[i-1] is not None and cci[i]<cci[i-1]
        combo=macd[i]<sig[i] and cdown
        belowvw=belowvw+1 if C[i]<vw[i] else 0
        if cr<=-hard: ix=i;reason='HARD';break
        if mode=='COMBO2':
            weakrun=weakrun+1 if combo else 0
            if weakrun>=2:ix=i;reason='COMBO2';break
        elif mode=='LOCKTRAIL':
            if pr>=0.30 and cr<=0:ix=i;reason='BE';break
            if pr>=0.50 and cr<=0.20:ix=i;reason='LOCK02';break
            if pr>=0.80 and dd<=-0.30:ix=i;reason='TRAIL03';break
        elif mode=='ADAPTIVE':
            # allow early noise; only structural weakness exits a losing/flat trade
            if pr<0.30:
                if belowvw>=2 and combo and cr<0:ix=i;reason='EARLY_STRUCT_FAIL';break
            else:
                if pr>=0.30 and cr<=0:ix=i;reason='BE';break
                if pr>=0.60 and cr<=0.25:ix=i;reason='LOCK25';break
                if pr>=1.00 and dd<=-0.35:ix=i;reason='TRAIL35';break
                if pr>=2.00 and dd<=-0.45:ix=i;reason='TRAIL45_2P';break
                if combo and pr>=0.60 and dd<=-0.20:ix=i;reason='MOMO_WEAK_PROFIT';break
        elif mode=='ADAPTIVE_LOOSE':
            if pr<0.30:
                if belowvw>=3 and combo and cr<0:ix=i;reason='EARLY_STRUCT_FAIL3';break
            else:
                if pr>=0.40 and cr<=0:ix=i;reason='BE';break
                if pr>=0.80 and cr<=0.30:ix=i;reason='LOCK30';break
                if pr>=1.20 and dd<=-0.45:ix=i;reason='TRAIL45';break
                if pr>=2.00 and dd<=-0.55:ix=i;reason='TRAIL55_2P';break
    mfe=pct(entry,max(H[i0:ix+1]));mae=pct(entry,min(L[i0:ix+1]));ret=pct(entry,C[ix])
    return {'ret_gross':ret,'mfe':mfe,'mae':mae,'giveback':max(0,mfe-ret),'reason':reason,'hold':ix-i0}

def metrics(ts,cost=.08):
    if not ts:return None
    r=[x['ret_gross']-cost for x in ts];w=[x for x in r if x>0];l=[x for x in r if x<0];gp=sum(w);gl=-sum(l);pf=gp/gl if gl else (999 if gp else 0)
    eq=pk=0;mdd=0
    for x in r:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return {'n':len(r),'win':100*len(w)/len(r),'avg':statistics.fmean(r),'pf':pf,'net':sum(r),'mdd':mdd,'mfe':statistics.fmean([x['mfe'] for x in ts]),'mae':statistics.fmean([x['mae'] for x in ts]),'giveback':statistics.fmean([x['giveback'] for x in ts])}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(DEFAULT_DB));ap.add_argument('--max-days',type=int,default=135);args=ap.parse_args();t0=time.time()
    con=sqlite3.connect(args.db);byd=defaultdict(list)
    for s in SYMS:
        dm=load_days(con,s,args.max_days);ds=sorted(dm);cnt=0
        for k in range(1,len(ds)):
            es=entries(dm[ds[k-1]],dm[ds[k]])
            for en,e in es.items():byd[ds[k]].append((s,en,e));cnt+=len(es)
        print('LOAD',s,'DAYS=',max(0,len(ds)-1),'CAND=',cnt)
    con.close()
    dates=sorted(byd);a=int(len(dates)*.6);b=int(len(dates)*.8);S={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('\n=== V129 FAILURE DECOMP + BROADER TOURNAMENT ===');print('DATES',len(dates),'SPLIT',{k:len(v) for k,v in S.items()})
    ens=sorted({en for rows in byd.values() for _,en,_ in rows});exs=['COMBO2','LOCKTRAIL','ADAPTIVE','ADAPTIVE_LOOSE'];stops=[1.0,1.25,1.5]
    book={(en,ex,st):{'IS':[],'OOS':[],'HOLDOUT':[]} for en in ens for ex in exs for st in stops}
    for d,rows in byd.items():
        lab='IS' if d in S['IS'] else ('OOS' if d in S['OOS'] else 'HOLDOUT')
        for s,en,e in rows:
            for ex in exs:
                for st in stops:
                    z=do_exit(e,ex,st);z.update(symbol=s,date=d,entry=en,exit_mode=ex,stop=st);book[(en,ex,st)][lab].append(z)
    rank=[]
    for key,v in book.items():
        iz=metrics(v['IS']);oz=metrics(v['OOS']);hz=metrics(v['HOLDOUT'])
        elig=bool(oz and hz and oz['n']>=15 and hz['n']>=15 and oz['avg']>0 and hz['avg']>0 and oz['pf']>1 and hz['pf']>1)
        sc=-1e9 if not elig else hz['avg']*.45+(hz['pf']-1)*.22+(hz['win']/100)*.1+hz['mdd']*.01-hz['giveback']*.05
        rank.append((sc,elig,key,iz,oz,hz,v))
    rank.sort(reverse=True,key=lambda x:x[0])
    print('\n=== TOP ROBUST CANDIDATES ===')
    for n,(sc,elig,key,iz,oz,hz,v) in enumerate(rank[:30],1):
        en,ex,st=key
        print(n,en,ex,'STOP',st,'ELIG',elig,'O_N',oz['n'] if oz else 0,'O_WIN',f"{oz['win']:.2f}" if oz else 'NA','O_AVG',f"{oz['avg']:.4f}" if oz else 'NA','O_PF',f"{oz['pf']:.3f}" if oz else 'NA','H_N',hz['n'] if hz else 0,'H_WIN',f"{hz['win']:.2f}" if hz else 'NA','H_AVG',f"{hz['avg']:.4f}" if hz else 'NA','H_PF',f"{hz['pf']:.3f}" if hz else 'NA','H_NET',f"{hz['net']:.2f}" if hz else 'NA','H_MDD',f"{hz['mdd']:.2f}" if hz else 'NA','H_GB',f"{hz['giveback']:.3f}" if hz else 'NA')
    # explain V128-style failure: best OOS vs best HOLDOUT divergence
    best_o=max(rank,key=lambda r:(r[4]['avg'] if r[4] else -999));best_h=max(rank,key=lambda r:(r[5]['avg'] if r[5] else -999))
    print('\n=== FAILURE DECOMPOSITION ===')
    for tag,r in [('BEST_OOS',best_o),('BEST_HOLDOUT',best_h)]:
        _,_,key,iz,oz,hz,v=r;print(tag,key,'OOS',oz,'HOLDOUT',hz)
    winner=next((r for r in rank if r[1]),None)
    if winner:
        _,_,key,iz,oz,hz,v=winner;print('\nWINNER',key)
        # per-symbol holdout contribution
        bys=defaultdict(list);reasons=defaultdict(int)
        for t in v['HOLDOUT']:bys[t['symbol']].append(t);reasons[t['reason']]+=1
        print('=== WINNER HOLDOUT BY SYMBOL ===')
        for s,ts in sorted(bys.items()):print(s,metrics(ts))
        print('=== WINNER EXIT REASONS ===');print(dict(sorted(reasons.items(),key=lambda kv:-kv[1])))
        final=bool(oz['pf']>=1.2 and hz['pf']>=1.2 and oz['avg']>0 and hz['avg']>0 and hz['mdd']>-8)
        print('FINAL_PASS=',final)
    else: print('\nWINNER NONE\nFINAL_PASS=False')
    print('ELAPSED_SEC',round(time.time()-t0,1))

if __name__=='__main__':main()
