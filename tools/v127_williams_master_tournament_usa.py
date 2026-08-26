#!/usr/bin/env python3
"""V127 Williams USA Master Tournament.

READ ONLY. NO API. NO ORDERS. NO DOWNLOADS.

Purpose
-------
Re-evaluate existing Williams research ideas on one fixed US master dataset.
The script deliberately separates ENTRY quality from EXIT quality and uses a
chronological IS/OOS/HOLDOUT split so the final winner is selected by the
untouched HOLDOUT segment, not by one cherry-picked trade.

Existing research reused here:
- V4/V5 first-morning Williams + volume + CCI + MACD-hist entry family.
- Williams trend-strength causal 3m/5m confirmation family.
- V5 MACD+CCI / 2-bar exit family.
- Williams V3 HYBRID_LOCK_IND / profit-lock family.

Exploratory dimension (must earn its place on HOLDOUT):
- early hard-loss caps of 0.75 / 1.00 / 1.25 / 1.50 percent.

No live-engine code is imported or modified.
"""
from __future__ import annotations
import argparse, csv, json, math, sqlite3, statistics, time
from pathlib import Path
from collections import defaultdict

ROOT=Path('/home/ubuntu/day-trader-api')
DEFAULT_DB=ROOT/'daytrader.db'
DEFAULT_SYMBOLS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
COST_BPS_DEFAULT=8.0  # round-trip friction assumption, 0.08%


def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]:out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals,period):
    n=len(vals); out=[None]*n
    if n<period+2:return out
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(period+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out

def cci(highs,lows,closes,period=20):
    tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(period-1,len(tp)):
        w=tp[i-period+1:i+1]; ma=sum(w)/period; md=sum(abs(x-ma) for x in w)/period
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def hhmm(et):
    s=str(et)
    if 'T' in s:s=s.split('T',1)[1]
    if ':' in s:
        p=s[:5].split(':'); return int(p[0])*100+int(p[1])
    d=''.join(ch for ch in s if ch.isdigit())
    if len(d)>=6:return int(d[-6:-2])
    return None

def pct(a,b):return (b/a-1.0)*100.0 if a else 0.0

def load_days(con,symbol,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if len(rows)>=300:out[str(d)]=rows
    return out

def arrays(rows):
    H=[float(r[2]) for r in rows]; L=[float(r[3]) for r in rows]; C=[float(r[4]) for r in rows]; V=[float(r[5] or 0) for r in rows]
    r2=rsi(C,2); r14=rsi(C,14); c20=cci(H,L,C,20)
    e12=ema(C,12); e26=ema(C,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    vwap=[]; pv=vv=0.0
    for h,l,c,v in zip(H,L,C,V):
        tp=(h+l+c)/3; pv+=tp*v; vv+=v; vwap.append(pv/vv if vv else c)
    return H,L,C,V,r2,r14,c20,macd,sig,hist,vwap

def base_arrows(prev,cur):
    if len(prev)<100 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    H,L,C,V,r2,r14,c20,macd,sig,hist,vwap=arrays(cur)
    out=[]
    for i in range(20,len(cur)-10):
        if not (C[i-1] <= trig < C[i]):continue
        if r2[i] is None or r2[i] <= 50:continue
        prior=V[max(0,i-10):i]; vavg=(sum(prior)/len(prior)) if prior else 0.0
        vr=(V[i]/vavg) if vavg>0 else 0.0
        t=hhmm(cur[i][0])
        out.append({'i':i,'time':str(cur[i][0]),'entry0':C[i],'trigger':trig,'H':H,'L':L,'C':C,'V':V,'r2':r2,'r14':r14,'cci':c20,'macd':macd,'sig':sig,'hist':hist,'vwap':vwap,'vr':vr,'hhmm':t})
    return out

def entry_candidates(prev,cur):
    """Return one candidate per entry family per day; all conditions causal."""
    arr=base_arrows(prev,cur)
    if not arr:return {}
    res={}
    # Legacy V5 strict first-cross family.
    first=arr[0]
    i=first['i']
    strict=(first['hhmm'] is not None and 930<=first['hhmm']<=1100 and first['vr']>=1.5 and first['cci'][i] is not None and first['cci'][i]>100 and i>=1 and first['hist'][i]>first['hist'][i-1])
    if strict:res['V5_STRICT']={**first,'entry_i':i,'entry':first['C'][i]}

    # Existing trend-strength concepts: wait 3m or 5m after raw arrow.
    # One entry per day prevents repeated cross-chasing.
    a=arr[0]; i=a['i']
    if i+3<len(a['C']):
        j=i+3; ret3=pct(a['C'][i],a['C'][j]); hist_acc=a['hist'][j]>a['hist'][i]; vwap_hold=all(a['C'][k]>=a['vwap'][k] for k in range(i+1,j+1))
        if ret3>=0.10 and hist_acc:
            res['TREND3_10_HIST']={**a,'entry_i':j,'entry':a['C'][j]}
        if ret3>=0.10 and hist_acc and vwap_hold:
            res['TREND3_10_HIST_VWAP']={**a,'entry_i':j,'entry':a['C'][j]}
    if i+5<len(a['C']):
        j=i+5; ret5=pct(a['C'][i],a['C'][j]); hist_acc=a['hist'][j]>a['hist'][i]; vwap_hold=all(a['C'][k]>=a['vwap'][k] for k in range(i+1,j+1))
        if ret5>=0.20 and hist_acc:
            res['TREND5_20_HIST']={**a,'entry_i':j,'entry':a['C'][j]}
        if ret5>=0.20 and hist_acc and vwap_hold:
            res['TREND5_20_HIST_VWAP']={**a,'entry_i':j,'entry':a['C'][j]}
        # Existing Strong5 family used in profit-lock OOS work.
        if ret5>=0.30:
            res['STRONG5_30']={**a,'entry_i':j,'entry':a['C'][j]}
    return res

def exit_trade(e,mode,hard_loss):
    i0=e['entry_i']; entry=e['entry']; H=e['H']; L=e['L']; C=e['C']; cci20=e['cci']; macd=e['macd']; sig=e['sig']
    peak=entry; weak_run=0
    reason='EOD'; ix=len(C)-1
    for i in range(i0+1,len(C)):
        peak=max(peak,H[i]); peak_ret=pct(entry,peak); cur_ret=pct(entry,C[i]); dd=pct(peak,C[i])
        # Early-loss cap: tested here, never assumed to be good.
        if cur_ret <= -abs(hard_loss):
            ix=i;reason=f'STOP_{hard_loss:.2f}';break
        cdown=bool(cci20[i] is not None and cci20[i-1] is not None and cci20[i]<cci20[i-1])
        combo=bool(macd[i]<sig[i] and cdown)
        if mode=='MACD_CCI_COMBO':
            if combo:ix=i;reason=mode;break
        elif mode=='COMBO_2BAR':
            weak_run=weak_run+1 if combo else 0
            if weak_run>=2:ix=i;reason=mode;break
        elif mode=='HYBRID_LOCK_IND':
            # Existing profit-lock logic: indicators only matter after profit exists.
            if peak_ret>=0.50 and cur_ret<=0.20:ix=i;reason='LOCK_05_TO_02';break
            if peak_ret>=0.30 and cur_ret<=0.00:ix=i;reason='LOCK_03_TO_BE';break
            if combo and peak_ret>=0.30:ix=i;reason='PROFIT_MOMENTUM_WEAK';break
            if peak_ret>=0.80 and dd<=-0.30:ix=i;reason='TRAIL_AFTER_08';break
        elif mode=='HYBRID_LOCK_TRAIL':
            if peak_ret>=0.30 and cur_ret<=0.00:ix=i;reason='LOCK_03_TO_BE';break
            if peak_ret>=0.50 and cur_ret<=0.20:ix=i;reason='LOCK_05_TO_02';break
            if peak_ret>=0.80 and dd<=-0.30:ix=i;reason='TRAIL_AFTER_08';break
    ret=pct(entry,C[ix]); mfe=pct(entry,max(H[i0:ix+1])); mae=pct(entry,min(L[i0:ix+1])); giveback=max(0.0,mfe-ret)
    return {'ret_gross':ret,'hold':ix-i0,'mfe':mfe,'mae':mae,'giveback':giveback,'reason':reason,'exit_i':ix,'exit':C[ix]}

def metrics(trades,cost_pct):
    if not trades:return None
    rs=[t['ret_gross']-cost_pct for t in trades]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    gp=sum(wins); gl=-sum(losses); pf=gp/gl if gl>0 else (999.0 if gp>0 else 0.0)
    eq=peak=0.0;mdd=0.0; streak=curst=0
    for x in rs:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
        if x<0:curst+=1;streak=max(streak,curst)
        else:curst=0
    return {'n':len(rs),'win':100*len(wins)/len(rs),'avg':statistics.fmean(rs),'med':statistics.median(rs),'avg_win':statistics.fmean(wins) if wins else 0.0,'avg_loss':statistics.fmean(losses) if losses else 0.0,'pf':pf,'mdd':mdd,'net':sum(rs),'mfe':statistics.fmean([t['mfe'] for t in trades]),'mae':statistics.fmean([t['mae'] for t in trades]),'giveback':statistics.fmean([t['giveback'] for t in trades]),'hold':statistics.fmean([t['hold'] for t in trades]),'loss_streak':streak}

def score(z):
    if not z or z['n']<20:return -1e9
    # Primary objective: positive expectancy + PF + win rate, penalize DD/giveback.
    return z['avg']*0.42 + (z['pf']-1.0)*0.18 + (z['win']/100.0)*0.14 + z['mdd']*0.012 - z['giveback']*0.06

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default=str(DEFAULT_DB));ap.add_argument('--max-days',type=int,default=135)
    ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS));ap.add_argument('--cost-bps',type=float,default=COST_BPS_DEFAULT)
    ap.add_argument('--top',type=int,default=25)
    args=ap.parse_args(); t0=time.time(); cost_pct=args.cost_bps/100.0
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); by_date=defaultdict(list); coverage={}
    for s in syms:
        dm=load_days(con,s,args.max_days);ds=sorted(dm);coverage[s]=max(0,len(ds)-1);cnt=0
        for di in range(1,len(ds)):
            d=ds[di]; ecs=entry_candidates(dm[ds[di-1]],dm[d]);cnt+=len(ecs)
            for ename,e in ecs.items():by_date[d].append((s,ename,e))
        print('LOAD',s,'DAYS=',coverage[s],'ENTRY_CANDIDATES=',cnt)
    con.close()
    dates=sorted(by_date); n=len(dates); a=int(n*0.60); b=int(n*0.80)
    split={'IS':set(dates[:a]),'OOS':set(dates[a:b]),'HOLDOUT':set(dates[b:])}
    print('\n=== V127 WILLIAMS MASTER TOURNAMENT USA ===')
    print('READ_ONLY=YES ORDERS=NONE DOWNLOADS=NONE')
    print('SYMBOLS=',len(syms),'DATES=',n,'SPLIT=',{k:len(v) for k,v in split.items()},'COST_BPS=',args.cost_bps)
    print('ENTRY_FAMILIES=V5_STRICT,TREND3_10_HIST,TREND3_10_HIST_VWAP,TREND5_20_HIST,TREND5_20_HIST_VWAP,STRONG5_30')
    print('EXIT_FAMILIES=MACD_CCI_COMBO,COMBO_2BAR,HYBRID_LOCK_IND,HYBRID_LOCK_TRAIL')
    print('EARLY_LOSS_CAPS=0.75,1.00,1.25,1.50')
    entries=sorted(set(en for rows in by_date.values() for _,en,_ in rows)); exits=['MACD_CCI_COMBO','COMBO_2BAR','HYBRID_LOCK_IND','HYBRID_LOCK_TRAIL']; stops=[0.75,1.0,1.25,1.5]
    combos={}
    for en in entries:
        for ex in exits:
            for st in stops:combos[(en,ex,st)]={'IS':[],'OOS':[],'HOLDOUT':[]}
    for d,rows in by_date.items():
        lab='IS' if d in split['IS'] else ('OOS' if d in split['OOS'] else 'HOLDOUT')
        for s,en,e in rows:
            for ex in exits:
                for st in stops:
                    tr=exit_trade(e,ex,st);tr.update(symbol=s,date=d,entry_family=en,exit_family=ex,stop=st)
                    combos[(en,ex,st)][lab].append(tr)
    results=[]
    for key,parts in combos.items():
        mi=metrics(parts['IS'],cost_pct);mo=metrics(parts['OOS'],cost_pct);mh=metrics(parts['HOLDOUT'],cost_pct)
        if not (mi and mo and mh):continue
        # Eligibility requires positive OOS before HOLDOUT ranking.
        eligible=bool(mo['n']>=20 and mo['avg']>0 and mo['pf']>1.0)
        hs=score(mh) if eligible else -1e9
        results.append({'entry':key[0],'exit':key[1],'stop':key[2],'eligible':eligible,'holdout_score':hs,'IS':mi,'OOS':mo,'HOLDOUT':mh})
    results.sort(key=lambda r:r['holdout_score'],reverse=True)
    print('\n=== TOP HOLDOUT RANK ===')
    for i,r in enumerate(results[:args.top],1):
        h=r['HOLDOUT'];o=r['OOS']
        print(i,r['entry'],r['exit'],'STOP=',r['stop'],'ELIG=',r['eligible'],'H_SCORE=',f"{r['holdout_score']:.4f}",
              'H_N=',h['n'],'H_WIN=',f"{h['win']:.2f}%",'H_AVG=',f"{h['avg']:.4f}%",'H_PF=',f"{h['pf']:.3f}",'H_NET=',f"{h['net']:.2f}%",'H_MDD=',f"{h['mdd']:.2f}%",'H_GIVEBACK=',f"{h['giveback']:.3f}%",
              'O_WIN=',f"{o['win']:.2f}%",'O_AVG=',f"{o['avg']:.4f}%",'O_PF=',f"{o['pf']:.3f}")
    outj=Path('/tmp/v127_williams_master_tournament.json');outc=Path('/tmp/v127_williams_master_tournament.csv')
    outj.write_text(json.dumps({'config':vars(args),'dates':dates,'coverage':coverage,'results':results},indent=2))
    with outc.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['rank','entry','exit','stop','eligible','h_score','h_n','h_win','h_avg','h_pf','h_net','h_mdd','h_mfe','h_mae','h_giveback','h_hold','o_n','o_win','o_avg','o_pf','is_n','is_win','is_avg','is_pf'])
        for i,r in enumerate(results,1):
            h,o,z=r['HOLDOUT'],r['OOS'],r['IS'];w.writerow([i,r['entry'],r['exit'],r['stop'],r['eligible'],r['holdout_score'],h['n'],h['win'],h['avg'],h['pf'],h['net'],h['mdd'],h['mfe'],h['mae'],h['giveback'],h['hold'],o['n'],o['win'],o['avg'],o['pf'],z['n'],z['win'],z['avg'],z['pf']])
    print('\nREPORT_JSON',outj);print('REPORT_CSV',outc);print('ELAPSED_SEC',round(time.time()-t0,1))
    if results:
        best=results[0];print('WINNER',best['entry'],best['exit'],'STOP=',best['stop'],'ELIGIBLE=',best['eligible'])
        print('NEXT=DO_NOT_DEPLOY; REVIEW WINNER + RUN ROBUSTNESS/OOS STRESS FIRST')

if __name__=='__main__':main()
