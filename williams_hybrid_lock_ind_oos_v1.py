#!/usr/bin/env python3
import argparse, sqlite3, statistics

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']

# Fixed strategy chosen from prior in-sample comparison:
# ENTRY: raw Williams arrow, then +0.30% confirmation after 5 minutes
# EXIT: fixed HYBRID_LOCK_IND logic copied conceptually from V3; no tuning here.


def ema(vals, span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


def rsi(vals, period=2):
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
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    return highs,lows,closes,r2,c20,macd,sig,hist


def entries(prev,cur):
    if len(prev)<100 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs,lows,closes,r2,c20,macd,sig,hist=indicators(cur)
    out=[]
    for i in range(20,len(cur)-10):
        if not (closes[i-1] <= trig < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        j=i+5
        if j>=len(cur): continue
        if pct(closes[i],closes[j]) < 0.30: continue
        out.append({'arrow_i':i,'i':j,'entry':closes[j],'highs':highs,'lows':lows,'closes':closes,'cci':c20,'macd':macd,'sig':sig,'hist':hist})
    return out


def exit_hybrid_lock_ind(e):
    i0=e['i']; entry=e['entry']; highs=e['highs']; closes=e['closes']; cci=e['cci']; macd=e['macd']; sig=e['sig']
    peak=entry; armed03=False; armed05=False
    for i in range(i0+1,len(closes)):
        peak=max(peak,highs[i])
        peak_ret=pct(entry,peak)
        cur_ret=pct(entry,closes[i])
        if peak_ret>=0.30: armed03=True
        if peak_ret>=0.50: armed05=True
        cci_down=bool(cci[i] is not None and cci[i-1] is not None and cci[i] < cci[i-1])
        indicator_weak=bool(macd[i] < sig[i] and cci_down)
        # fixed HYBRID_LOCK_IND: once modest profit exists, protect gains; if momentum weakens, exit.
        if armed05 and cur_ret <= 0.20: return i
        if armed03 and cur_ret <= 0.00: return i
        if indicator_weak and peak_ret>=0.30: return i
        if peak_ret>=0.80 and pct(peak,closes[i]) <= -0.30: return i
    return len(closes)-1


def metrics(trades):
    if not trades:return None
    rets=[x['ret'] for x in trades]; wins=[x for x in rets if x>0]; losses=[x for x in rets if x<0]
    gp=sum(wins); gl=-sum(losses); pf=(gp/gl) if gl>0 else (999.0 if gp>0 else 0.0)
    eq=0.0; peak=0.0; mdd=0.0
    for x in rets:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(trades),'avg':statistics.fmean(rets),'win':100*len(wins)/len(rets),'pf':pf,'mdd':mdd,'avg_hold':statistics.fmean([x['hold'] for x in trades])}


def show(label,z):
    if not z:
        print(label,'N=0'); return
    print(label,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%",'AVG_HOLD=',f"{z['avg_hold']:.1f}m")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); raw=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); cnt=0
        for di in range(1,len(ds)):
            for e in entries(dm[ds[di-1]],dm[ds[di]]):
                ix=exit_hybrid_lock_ind(e); ret=pct(e['entry'],e['closes'][ix]); hold=ix-e['i']
                raw.append({'symbol':s,'date':str(ds[di]),'ret':ret,'hold':hold}); cnt+=1
        print('AUDIT',s,'ENTRIES=',cnt)
    con.close()
    dates=sorted(set(x['date'] for x in raw)); split=len(dates)//2; isd=set(dates[:split]); oosd=set(dates[split:])
    print('\n=== WILLIAMS HYBRID_LOCK_IND FIXED OOS V1 ===')
    print('ENTRY=Williams arrow + 5m confirmation >= +0.30%')
    print('EXIT=HYBRID_LOCK_IND fixed; no re-tuning in this script')
    print('DATE_RANGE=',(dates[0] if dates else None,dates[-1] if dates else None),'UNIQUE_DATES=',len(dates),'IS_DATES=',len(isd),'OOS_DATES=',len(oosd))
    allz=metrics(raw); isz=metrics([x for x in raw if x['date'] in isd]); oosz=metrics([x for x in raw if x['date'] in oosd])
    show('ALL',allz); show('IS ',isz); show('OOS',oosz)
    if oosz:
        pass_flag=bool(oosz['n']>=50 and oosz['avg']>0 and oosz['pf']>=1.5 and oosz['mdd']>-15)
        print('OOS_PASS=',pass_flag)

if __name__=='__main__': main()
