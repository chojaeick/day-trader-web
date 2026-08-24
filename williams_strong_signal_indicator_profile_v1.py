#!/usr/bin/env python3
import argparse, sqlite3, statistics, math

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']

def ema(vals,span):
    if not vals:return []
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

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,symbol,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out

def collect(prev,cur,symbol,date):
    if len(prev)<100 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]
    r2=rsi(closes,2); r14=rsi(closes,14); c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    out=[]
    for i in range(20,len(cur)-60):
        if not (closes[i-1] <= trig < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        entry=closes[i]
        mfe20=pct(entry,max(highs[i:min(len(cur),i+21)]))
        mfe60=pct(entry,max(highs[i:min(len(cur),i+61)]))
        hs=(hist[i]-hist[i-1]) if i>0 else 0.0
        hacc=(hist[i]-2*hist[i-1]+hist[i-2]) if i>1 else 0.0
        gap=macd[i]-sig[i]
        out.append({
            'symbol':symbol,'date':date,'time':cur[i][0],
            'rsi2':r2[i],'rsi14':r14[i],'cci':c20[i],
            'macd':macd[i],'sig':sig[i],'gap':gap,'hist':hist[i],
            'hist_slope':hs,'hist_accel':hacc,
            'strong20':mfe20>=0.50,'strong60':mfe60>=1.00
        })
    return out

def q(v,p):
    if not v:return float('nan')
    s=sorted(v); x=(len(s)-1)*p; a=int(math.floor(x)); b=int(math.ceil(x))
    if a==b:return s[a]
    return s[a]*(b-x)+s[b]*(x-a)

def fmt(x):
    return 'NA' if x is None or (isinstance(x,float) and math.isnan(x)) else f'{x:.4f}'

def profile(name, rows):
    print('\n===',name,'N=',len(rows),'===')
    fields=['rsi2','rsi14','cci','gap','hist','hist_slope','hist_accel']
    for f in fields:
        vals=[x[f] for x in rows if x[f] is not None]
        if not vals: continue
        print(f, 'P10=',fmt(q(vals,.10)),'P25=',fmt(q(vals,.25)),'MED=',fmt(q(vals,.50)),'P75=',fmt(q(vals,.75)),'P90=',fmt(q(vals,.90)),'AVG=',fmt(statistics.fmean(vals)))

def bucket_report(rows, field, buckets):
    print('\n---',field,'BUCKETS ---')
    for lo,hi,label in buckets:
        z=[x for x in rows if x[field] is not None and x[field]>=lo and (hi is None or x[field]<hi)]
        if not z: continue
        s20=100*sum(x['strong20'] for x in z)/len(z); s60=100*sum(x['strong60'] for x in z)/len(z)
        print(label,'N=',len(z),'STR20=',f'{s20:.2f}%','STR60=',f'{s60:.2f}%')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); rows=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); n0=len(rows)
        for di in range(1,len(ds)):
            rows.extend(collect(dm[ds[di-1]],dm[ds[di]],s,ds[di]))
        print('AUDIT',s,'SIGNALS=',len(rows)-n0)
    con.close()

    strong20=[x for x in rows if x['strong20']]
    weak20=[x for x in rows if not x['strong20']]
    strong60=[x for x in rows if x['strong60']]
    weak60=[x for x in rows if not x['strong60']]

    print('\n=== WILLIAMS STRONG SIGNAL INDICATOR PROFILE V1 ===')
    print('RULE=Williams CrossUp + RSI2>50. Profiles are measured at signal time only.')
    print('TOTAL=',len(rows),'STRONG20=',len(strong20),'STRONG60=',len(strong60))
    profile('STRONG20',strong20); profile('WEAK20',weak20)
    profile('STRONG60',strong60); profile('WEAK60',weak60)

    bucket_report(rows,'rsi2',[(50,60,'50-60'),(60,70,'60-70'),(70,80,'70-80'),(80,90,'80-90'),(90,95,'90-95'),(95,None,'95+')])
    bucket_report(rows,'rsi14',[(0,40,'<40'),(40,50,'40-50'),(50,60,'50-60'),(60,70,'60-70'),(70,None,'70+')])
    bucket_report(rows,'cci',[(-1e9,0,'<0'),(0,50,'0-50'),(50,100,'50-100'),(100,150,'100-150'),(150,200,'150-200'),(200,None,'200+')])

    tests=[
        ('MACD>SIGNAL',lambda x:x['gap']>0),
        ('HIST>0',lambda x:x['hist']>0),
        ('HIST_SLOPE>0',lambda x:x['hist_slope']>0),
        ('HIST_ACCEL>0',lambda x:x['hist_accel']>0),
        ('MACD>SIGNAL + HIST_SLOPE>0',lambda x:x['gap']>0 and x['hist_slope']>0),
        ('RSI2>=90 + MACD>SIGNAL',lambda x:x['rsi2']>=90 and x['gap']>0),
        ('RSI2>=90 + HIST_SLOPE>0',lambda x:x['rsi2']>=90 and x['hist_slope']>0),
        ('CCI>=100 + MACD>SIGNAL',lambda x:x['cci'] is not None and x['cci']>=100 and x['gap']>0),
    ]
    print('\n=== SIMPLE SIGNAL-TIME FILTERS ===')
    base20=100*len(strong20)/len(rows) if rows else 0
    base60=100*len(strong60)/len(rows) if rows else 0
    for name,fn in tests:
        z=[x for x in rows if fn(x)]
        if not z: continue
        s20=100*sum(x['strong20'] for x in z)/len(z); s60=100*sum(x['strong60'] for x in z)/len(z)
        print(name,'N=',len(z),'STR20=',f'{s20:.2f}%','LIFT20=',f'{s20/base20:.2f}x' if base20 else 'NA','STR60=',f'{s60:.2f}%','LIFT60=',f'{s60/base60:.2f}x' if base60 else 'NA')

if __name__=='__main__': main()
