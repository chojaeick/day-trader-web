#!/usr/bin/env python3
import argparse, sqlite3, statistics

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
MODES=['LOCK_BE','LOCK_03','LOCK_05','TRAIL_03','TRAIL_04','HYBRID_LOCK_TRAIL','HYBRID_LOCK_IND']


def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
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
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(period-1,len(tp)):
        w=tp[i-period+1:i+1]; ma=sum(w)/period; md=sum(abs(x-ma) for x in w)/period
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out


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
    r2=rsi(closes,2); c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9)
    return highs,lows,closes,r2,c20,macd,sig


def strong5_entries(prev,cur):
    if len(prev)<100 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs,lows,closes,r2,c20,macd,sig=indicators(cur)
    out=[]
    for i in range(20,len(cur)-6):
        if not (closes[i-1] <= trig < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        j=i+5
        if pct(closes[i],closes[j]) < 0.30: continue
        out.append({'i':j,'arrow_i':i,'entry':closes[j],'highs':highs,'lows':lows,'closes':closes,'cci':c20,'macd':macd,'sig':sig,'rows':cur})
    return out


def exit_idx(e,mode):
    i0=e['i']; entry=e['entry']; highs=e['highs']; lows=e['lows']; closes=e['closes']; cci=e['cci']; macd=e['macd']; sig=e['sig']
    peak=entry; armed03=False; armed05=False; armed08=False
    for i in range(i0+1,len(closes)):
        peak=max(peak,highs[i]); gain=pct(entry,peak); cur=pct(entry,closes[i]); dd=pct(peak,closes[i])
        if gain>=0.30: armed03=True
        if gain>=0.50: armed05=True
        if gain>=0.80: armed08=True
        cci_down=bool(cci[i] is not None and cci[i-1] is not None and cci[i] < cci[i-1])
        weak=bool(macd[i] < sig[i] and cci_down)

        if mode=='LOCK_BE':
            if armed03 and cur <= 0.00:return i
        elif mode=='LOCK_03':
            if armed05 and cur <= 0.30:return i
        elif mode=='LOCK_05':
            if armed08 and cur <= 0.50:return i
        elif mode=='TRAIL_03':
            if armed03 and dd <= -0.30:return i
        elif mode=='TRAIL_04':
            if armed03 and dd <= -0.40:return i
        elif mode=='HYBRID_LOCK_TRAIL':
            if armed03 and cur <= 0.00:return i
            if armed05 and cur <= 0.20:return i
            if armed08 and dd <= -0.30:return i
        elif mode=='HYBRID_LOCK_IND':
            if armed03 and cur <= 0.00:return i
            if armed05 and cur <= 0.20:return i
            if armed08 and dd <= -0.35:return i
            if weak and gain>=0.30:return i
    return len(closes)-1


def metrics(trades):
    if not trades:return None
    rets=[t['ret'] for t in trades]; wins=[x for x in rets if x>0]; losses=[x for x in rets if x<0]
    gp=sum(wins); gl=-sum(losses); pf=(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0)
    eq=0.0; peak=0.0; mdd=0.0
    for x in rets:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    caps=[t['cap'] for t in trades if t['cap'] is not None]
    return {'n':len(trades),'avg':statistics.fmean(rets),'win':100*len(wins)/len(rets),'pf':pf,'mdd':mdd,'hold':statistics.fmean([t['hold'] for t in trades]),'cap':statistics.fmean(caps) if caps else None}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); raw={m:[] for m in MODES}
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); cnt=0
        for di in range(1,len(ds)):
            es=strong5_entries(dm[ds[di-1]],dm[ds[di]])
            cnt+=len(es)
            for e in es:
                for m in MODES:
                    ix=exit_idx(e,m); ret=pct(e['entry'],e['closes'][ix]); hold=ix-e['i']
                    mfe=max(0.0,pct(e['entry'],max(e['highs'][e['i']:ix+1])))
                    cap=(min(100.0,max(0.0,ret)/mfe*100.0) if mfe>0 else None)
                    raw[m].append({'ret':ret,'hold':hold,'cap':cap})
        print('AUDIT',s,'STRONG5_ENTRIES=',cnt)
    con.close()

    print('\n=== WILLIAMS EXIT V3 PROFIT-LOCK COMPARE ===')
    print('ENTRY=Williams arrow confirmed by +0.30% after 5m')
    rank=[]
    for m in MODES:
        z=metrics(raw[m])
        if not z: continue
        print(m,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%",'AVG_HOLD=',f"{z['hold']:.1f}m",'MFE_CAPTURE=',('NA' if z['cap'] is None else f"{z['cap']:.1f}%"))
        score=z['avg']*0.50+(z['pf']-1)*0.22+(z['win']/100)*0.08+z['mdd']*0.015+(z['cap'] or 0)/100*0.05
        rank.append((score,m,z))
    rank.sort(reverse=True,key=lambda x:x[0])
    print('\n=== EXIT QUALITY RANK ===')
    for i,(sc,m,z) in enumerate(rank,1):
        print(i,m,'SCORE=',f'{sc:.4f}','AVG=',f"{z['avg']:.4f}",'PF=',f"{z['pf']:.3f}",'WIN=',f"{z['win']:.2f}",'MDD=',f"{z['mdd']:.4f}",'CAP=',('NA' if z['cap'] is None else f"{z['cap']:.1f}"))

if __name__=='__main__': main()
