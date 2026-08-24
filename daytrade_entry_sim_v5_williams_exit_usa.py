#!/usr/bin/env python3
import argparse, sqlite3, statistics, math
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']


def ema(vals, span):
    if not vals: return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals, period):
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
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(period-1,len(tp)):
        w=tp[i-period+1:i+1]; ma=sum(w)/period; md=sum(abs(x-ma) for x in w)/period
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def hhmm(et):
    s=str(et)
    if 'T' in s: s=s.split('T',1)[1]
    if ':' in s:
        p=s[:5].split(':'); return int(p[0])*100+int(p[1])
    d=''.join(ch for ch in s if ch.isdigit())
    if len(d)>=6:return int(d[-6:-2])
    return None

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,symbol,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out

def indicators(rows):
    highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]; closes=[float(r[4]) for r in rows]
    r2=rsi(closes,2); r14=rsi(closes,14); c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    return highs,lows,closes,r2,r14,c20,macd,sig,hist

def find_entry(prev,cur):
    if len(prev)<100 or len(cur)<40:return None
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs,lows,closes,r2,r14,c20,macd,sig,hist=indicators(cur)
    vols=[float(r[5] or 0) for r in cur]
    first_cross_seen=False
    for i in range(20,len(cur)):
        cross=closes[i-1] <= trig < closes[i]
        if not cross or r2[i] is None or r2[i] <= 50: continue
        if first_cross_seen: continue
        first_cross_seen=True
        t=hhmm(cur[i][0])
        if t is None or not (930 <= t <= 1100): return None
        prior=vols[max(0,i-10):i]; vavg=sum(prior)/len(prior) if prior else 0
        if not (vavg>0 and vols[i] >= 1.5*vavg): return None
        if c20[i] is None or c20[i] <= 100:return None
        if i<2 or not (hist[i] > hist[i-1]):return None
        return {'i':i,'entry':closes[i],'trigger':trig,'cci':c20,'macd':macd,'sig':sig,'hist':hist,'highs':highs,'lows':lows,'closes':closes,'rows':cur}
    return None

def exit_index(e, mode):
    i0=e['i']; cci=e['cci']; macd=e['macd']; sig=e['sig']; hist=e['hist']; rows=e['rows']
    weak_run=0
    for i in range(i0+1,len(rows)):
        cci_down=bool(i>=2 and cci[i] is not None and cci[i-1] is not None and cci[i] < cci[i-1])
        macd_below=bool(macd[i] < sig[i])
        hist_neg=bool(hist[i] < 0)
        cci_below0=bool(cci[i] is not None and cci[i] < 0)
        combo=macd_below and cci_down
        if mode=='MACD_BELOW' and macd_below:return i
        if mode=='CCI_DOWN_2BAR':
            weak_run = weak_run+1 if cci_down else 0
            if weak_run>=2:return i
        elif mode=='MACD_CCI_COMBO' and combo:return i
        elif mode=='COMBO_2BAR':
            weak_run = weak_run+1 if combo else 0
            if weak_run>=2:return i
        elif mode=='HYBRID_HARD':
            if macd_below and hist_neg and cci_below0:return i
            weak_run = weak_run+1 if combo else 0
            if weak_run>=2:return i
    return len(rows)-1

def trade_metrics(trades):
    if not trades:return {}
    rets=[t['ret'] for t in trades]; wins=[x for x in rets if x>0]; losses=[x for x in rets if x<0]
    gp=sum(wins); gl=-sum(losses); pf=(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0)
    eq=0.0; peak=0.0; mdd=0.0
    for x in rets:
        eq += x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    holds=[t['hold'] for t in trades]; caps=[t['capture'] for t in trades if t['capture'] is not None]
    return {'n':len(trades),'avg':statistics.fmean(rets),'win':100*len(wins)/len(rets),'pf':pf,'mdd':mdd,'avg_hold':statistics.fmean(holds),'med_hold':statistics.median(holds),'capture':statistics.fmean(caps) if caps else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    modes=['MACD_BELOW','CCI_DOWN_2BAR','MACD_CCI_COMBO','COMBO_2BAR','HYBRID_HARD']
    alltr={m:[] for m in modes}; con=sqlite3.connect(args.db); entries=0
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); sc=0
        for di in range(1,len(ds)):
            e=find_entry(dm[ds[di-1]],dm[ds[di]])
            if not e: continue
            sc+=1; entries+=1
            i0=e['i']; entry=e['entry']
            for m in modes:
                ix=exit_index(e,m); xp=e['closes'][ix]; ret=pct(entry,xp); hold=ix-i0
                future_high=max(e['highs'][i0:ix+1]) if ix>=i0 else entry; mfe=pct(entry,future_high)
                capture=(ret/mfe*100) if mfe>0 else None
                alltr[m].append({'symbol':s,'date':ds[di],'ret':ret,'hold':hold,'capture':capture})
        print('AUDIT',s,'DAYS=',max(0,len(ds)-1),'ENTRIES=',sc)
    con.close()
    print('\n=== WILLIAMS V5 ENTRY+EXIT SIM USA ===')
    print('ENTRY=FIRST Williams CrossUp + 09:30-11:00 + volume>=1.5x prior10 + RSI2>50 + CCI20>100 + MACD hist rising')
    print('TOTAL_ENTRIES=',entries)
    ranked=[]
    for m in modes:
        z=trade_metrics(alltr[m])
        if not z: continue
        print(m,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%",'AVG_HOLD=',f"{z['avg_hold']:.1f}m",'MED_HOLD=',f"{z['med_hold']:.1f}m",'MFE_CAPTURE=',('NA' if z['capture'] is None else f"{z['capture']:.1f}%"))
        score=z['avg']*0.35 + (z['pf']-1)*0.15 + z['win']/100*0.10 + z['mdd']*0.02
        ranked.append((score,m,z))
    ranked.sort(reverse=True,key=lambda x:x[0])
    print('\n=== EXIT QUALITY RANK ===')
    for n,(sc,m,z) in enumerate(ranked,1): print(n,m,'SCORE=',f'{sc:.4f}','AVG=',f"{z['avg']:.4f}",'PF=',f"{z['pf']:.3f}",'WIN=',f"{z['win']:.2f}",'MDD=',f"{z['mdd']:.4f}")

if __name__=='__main__': main()
