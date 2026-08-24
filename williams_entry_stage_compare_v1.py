#!/usr/bin/env python3
import argparse, sqlite3, statistics

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
STAGES=['EARLY','CONFIRMED_3M','STRONG_5M']


def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


def rsi(vals,period=2):
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


def raw_signals(prev,cur):
    if len(prev)<100 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]
    r2=rsi(closes,2); c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9)
    out=[]
    for i in range(20,len(cur)-65):
        if not (closes[i-1] <= trig < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        out.append({'i':i,'highs':highs,'lows':lows,'closes':closes,'cci':c20,'macd':macd,'sig':sig,'rows':cur})
    return out


def exit_index(e,start_i):
    cci=e['cci']; macd=e['macd']; sig=e['sig']; rows=e['rows']
    for i in range(start_i+1,len(rows)):
        cci_down=bool(cci[i] is not None and cci[i-1] is not None and cci[i] < cci[i-1])
        if macd[i] < sig[i] and cci_down:
            return i
    return len(rows)-1


def stage_entry(e,stage):
    i=e['i']; closes=e['closes']
    if stage=='EARLY': return i
    if stage=='CONFIRMED_3M':
        j=i+3
        if j>=len(closes): return None
        if pct(closes[i],closes[j]) >= 0.20: return j
        return None
    if stage=='STRONG_5M':
        j=i+5
        if j>=len(closes): return None
        if pct(closes[i],closes[j]) >= 0.30: return j
        return None
    return None


def metrics(trades):
    if not trades:return None
    rets=[x['ret'] for x in trades]; wins=[x for x in rets if x>0]; losses=[x for x in rets if x<0]
    gp=sum(wins); gl=-sum(losses); pf=(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0)
    eq=0.0; peak=0.0; mdd=0.0
    for x in rets:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    holds=[x['hold'] for x in trades]
    return {'n':len(trades),'avg':statistics.fmean(rets),'win':100*len(wins)/len(rets),'pf':pf,'mdd':mdd,'avg_hold':statistics.fmean(holds),'med_hold':statistics.median(holds)}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); alltr={s:[] for s in STAGES}; raw_n=0
    for sym in syms:
        dm=load_days(con,sym,args.max_days); ds=sorted(dm); counts={s:0 for s in STAGES}; rs=0
        for di in range(1,len(ds)):
            for e in raw_signals(dm[ds[di-1]],dm[ds[di]]):
                rs+=1; raw_n+=1
                for stage in STAGES:
                    ei=stage_entry(e,stage)
                    if ei is None: continue
                    xi=exit_index(e,ei)
                    entry=e['closes'][ei]; xp=e['closes'][xi]
                    alltr[stage].append({'symbol':sym,'date':ds[di],'ret':pct(entry,xp),'hold':xi-ei})
                    counts[stage]+=1
        print('AUDIT',sym,'RAW=',rs,*(f'{k}={v}' for k,v in counts.items()))
    con.close()

    print('\n=== WILLIAMS ENTRY STAGE COMPARE V1 ===')
    print('RAW=Williams CrossUp + RSI2>50')
    print('EARLY=buy at arrow; CONFIRMED_3M=buy only if +0.20% after 3m; STRONG_5M=buy only if +0.30% after 5m')
    print('EXIT=MACD < signal AND CCI slope down')
    print('RAW_SIGNALS=',raw_n)
    ranked=[]
    for stage in STAGES:
        z=metrics(alltr[stage])
        if not z:
            print(stage,'N=0'); continue
        print(stage,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%",'AVG_HOLD=',f"{z['avg_hold']:.1f}m",'MED_HOLD=',f"{z['med_hold']:.1f}m")
        score=z['avg']*0.45+(z['pf']-1)*0.20+(z['win']/100)*0.10+z['mdd']*0.02
        ranked.append((score,stage,z))
    ranked.sort(reverse=True,key=lambda x:x[0])
    print('\n=== QUALITY RANK ===')
    for i,(sc,stage,z) in enumerate(ranked,1):
        print(i,stage,'SCORE=',f'{sc:.4f}','N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%")

if __name__=='__main__': main()
