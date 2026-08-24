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


def slope1(arr,i):
    if i<1 or arr[i] is None or arr[i-1] is None:return None
    return arr[i]-arr[i-1]


def slope3(arr,i):
    if i<3 or arr[i] is None or arr[i-3] is None:return None
    return (arr[i]-arr[i-3])/3.0


def accel(arr,i):
    if i<2 or arr[i] is None or arr[i-1] is None or arr[i-2] is None:return None
    return (arr[i]-arr[i-1])-(arr[i-1]-arr[i-2])


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
        mfe20=pct(entry,max(highs[i:i+21])); mfe60=pct(entry,max(highs[i:i+61]))
        out.append({
            'symbol':symbol,'date':date,'time':cur[i][0],
            'rsi2_s1':slope1(r2,i),'rsi2_s3':slope3(r2,i),'rsi2_acc':accel(r2,i),
            'rsi14_s1':slope1(r14,i),'rsi14_s3':slope3(r14,i),'rsi14_acc':accel(r14,i),
            'cci_s1':slope1(c20,i),'cci_s3':slope3(c20,i),'cci_acc':accel(c20,i),
            'hist_s1':slope1(hist,i),'hist_s3':slope3(hist,i),'hist_acc':accel(hist,i),
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


def profile(name,rows):
    print('\n===',name,'N=',len(rows),'===')
    fields=['rsi2_s1','rsi2_s3','rsi2_acc','rsi14_s1','rsi14_s3','rsi14_acc','cci_s1','cci_s3','cci_acc','hist_s1','hist_s3','hist_acc']
    for f in fields:
        v=[x[f] for x in rows if x[f] is not None]
        if not v:continue
        print(f,'P25=',fmt(q(v,.25)),'MED=',fmt(q(v,.50)),'P75=',fmt(q(v,.75)),'AVG=',fmt(statistics.fmean(v)))


def test(rows,name,fn,base20,base60):
    z=[x for x in rows if fn(x)]
    if not z:return None
    s20=100*sum(x['strong20'] for x in z)/len(z); s60=100*sum(x['strong60'] for x in z)/len(z)
    return (name,len(z),s20,s60,(s20/base20 if base20 else 0),(s60/base60 if base60 else 0))


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

    strong20=[x for x in rows if x['strong20']]; weak20=[x for x in rows if not x['strong20']]
    strong60=[x for x in rows if x['strong60']]; weak60=[x for x in rows if not x['strong60']]
    base20=100*len(strong20)/len(rows) if rows else 0; base60=100*len(strong60)/len(rows) if rows else 0
    print('\n=== WILLIAMS INDICATOR SLOPE PROFILE V2 ===')
    print('TOTAL=',len(rows),'BASE_STRONG20=',f'{base20:.2f}%','BASE_STRONG60=',f'{base60:.2f}%')
    profile('STRONG20',strong20); profile('WEAK20',weak20)
    profile('STRONG60',strong60); profile('WEAK60',weak60)

    tests=[
        ('RSI2_S1>0',lambda x:(x['rsi2_s1'] or -1e9)>0),
        ('RSI2_S3>0',lambda x:(x['rsi2_s3'] or -1e9)>0),
        ('RSI14_S1>0',lambda x:(x['rsi14_s1'] or -1e9)>0),
        ('RSI14_S3>0',lambda x:(x['rsi14_s3'] or -1e9)>0),
        ('CCI_S1>0',lambda x:(x['cci_s1'] or -1e9)>0),
        ('CCI_S3>0',lambda x:(x['cci_s3'] or -1e9)>0),
        ('CCI_S1>20',lambda x:(x['cci_s1'] or -1e9)>20),
        ('CCI_S3>10',lambda x:(x['cci_s3'] or -1e9)>10),
        ('RSI14_S3>0+CCI_S3>0',lambda x:(x['rsi14_s3'] or -1e9)>0 and (x['cci_s3'] or -1e9)>0),
        ('RSI14_S3>0+CCI_S3>10',lambda x:(x['rsi14_s3'] or -1e9)>0 and (x['cci_s3'] or -1e9)>10),
        ('CCI_S3>0+HIST_S3>0',lambda x:(x['cci_s3'] or -1e9)>0 and (x['hist_s3'] or -1e9)>0),
        ('RSI14_S3>0+CCI_S3>0+HIST_S3>0',lambda x:(x['rsi14_s3'] or -1e9)>0 and (x['cci_s3'] or -1e9)>0 and (x['hist_s3'] or -1e9)>0),
        ('RSI14_ACC>0+CCI_ACC>0',lambda x:(x['rsi14_acc'] or -1e9)>0 and (x['cci_acc'] or -1e9)>0),
    ]
    results=[]
    print('\n=== SLOPE FILTER LIFT ===')
    for name,fn in tests:
        r=test(rows,name,fn,base20,base60)
        if not r:continue
        results.append(r)
        print(r[0],'N=',r[1],'STR20=',f'{r[2]:.2f}%','LIFT20=',f'{r[4]:.2f}x','STR60=',f'{r[3]:.2f}%','LIFT60=',f'{r[5]:.2f}x')
    results.sort(key=lambda r:(r[4]+r[5]),reverse=True)
    print('\n=== TOP SLOPE FILTERS ===')
    for i,r in enumerate(results[:10],1):
        print(i,r[0],'N=',r[1],'STR20=',f'{r[2]:.2f}%','STR60=',f'{r[3]:.2f}%','AVG_LIFT=',f'{(r[4]+r[5])/2:.2f}x')

if __name__=='__main__': main()
