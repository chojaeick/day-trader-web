#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']
HORIZONS=(1,3,5,10,20)


def pct(a,b):
    return None if not a else (b/a-1.0)*100.0


def avg(xs):
    xs=[x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def win(xs):
    xs=[x for x in xs if x is not None]
    return 100.0*sum(1 for x in xs if x>0)/len(xs) if xs else None


def fmt(x):
    return 'NA' if x is None else f'{x:.4f}'


def rsi_series(closes, period):
    n=len(closes); out=[None]*n
    if n<period+1: return out
    gains=[]; losses=[]
    for i in range(1,n):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0.0)); losses.append(max(-d,0.0))
    ag=sum(gains[:period])/period; al=sum(losses[:period])/period
    out[period]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    for i in range(period+1,n):
        d=closes[i]-closes[i-1]; g=max(d,0.0); l=max(-d,0.0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    return out


def cci_series(highs,lows,closes,period=20):
    n=len(closes); out=[None]*n
    tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
    for i in range(period-1,n):
        w=tp[i-period+1:i+1]
        ma=sum(w)/period
        md=sum(abs(x-ma) for x in w)/period
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out


def load_symbol(con,symbol,max_days):
    dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    dates=sorted(dates)
    out={}
    for d in dates:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out


def hhmm_of(et):
    s=str(et).strip()
    try:
        if 'T' in s: return int(s.split('T',1)[1][:5].replace(':',''))
        if ':' in s: return int(s[:5].replace(':',''))
        if s.isdigit() and len(s)>=6: return int(s[-6:-2])
        if s.isdigit() and len(s)>=4: return int(s[:4])
    except Exception:
        return None
    return None


def generate(symbol,daymap):
    dates=sorted(daymap); out=[]
    for di in range(1,len(dates)):
        prev=daymap[dates[di-1]]; cur=daymap[dates[di]]
        if len(prev)<100 or len(cur)<30: continue
        prev_hi=max(float(r[2]) for r in prev); prev_lo=min(float(r[3]) for r in prev)
        trigger=float(cur[0][1])+0.5*(prev_hi-prev_lo)
        highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]; vols=[float(r[5] or 0) for r in cur]
        r2=rsi_series(closes,2); r14=rsi_series(closes,14); c20=cci_series(highs,lows,closes,20)
        first_seen=False
        for i in range(1,len(cur)):
            cross=closes[i-1] <= trigger and closes[i] > trigger
            if not cross or r2[i] is None or r2[i] <= 50: continue
            prior=vols[max(0,i-10):i]; vavg=sum(prior)/len(prior) if prior else 0
            vol_ok=bool(vavg>0 and vols[i]>=1.5*vavg)
            hhmm=hhmm_of(cur[i][0]); morning=bool(hhmm is not None and 930 <= hhmm <= 1100)
            e={'symbol':symbol,'date':dates[di],'time':str(cur[i][0]),'entry':closes[i],'trigger':trigger,
               'first':not first_seen,'morning':morning,'vol_ok':vol_ok,'rsi2':r2[i],'rsi14':r14[i],'cci20':c20[i]}
            for h in HORIZONS:
                e[f'r{h}']=pct(closes[i],closes[i+h]) if i+h<len(cur) else None
            fut=cur[i:min(len(cur),i+21)]
            e['mfe20']=max((float(x[2])/closes[i]-1)*100 for x in fut) if fut else None
            e['mae20']=min((float(x[3])/closes[i]-1)*100 for x in fut) if fut else None
            out.append(e); first_seen=True
    return out


def summarize(name,rows):
    sm={'name':name,'n':len(rows)}
    for h in HORIZONS:
        vals=[x[f'r{h}'] for x in rows if x[f'r{h}'] is not None]
        sm[f'r{h}']=avg(vals); sm[f'w{h}']=win(vals)
    sm['mfe']=avg([x['mfe20'] for x in rows]); sm['mae']=avg([x['mae20'] for x in rows])
    return sm


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    con=sqlite3.connect(args.db); allsig=[]
    for s in [x.strip().upper() for x in args.symbols.split(',') if x.strip()]:
        dm=load_symbol(con,s,args.max_days); ss=generate(s,dm); allsig.extend(ss)
        print('AUDIT',s,'DAYS=',max(0,len(dm)-1),'SIGNALS=',len(ss))
    con.close()

    base=lambda x: x['first'] and x['morning'] and x['vol_ok']
    variants={
        'BASE_FIRST_MORNING_VOLUME': lambda x: base(x),
        'BASE_RSI2_60': lambda x: base(x) and x['rsi2']>=60,
        'BASE_RSI2_70': lambda x: base(x) and x['rsi2']>=70,
        'BASE_RSI2_80': lambda x: base(x) and x['rsi2']>=80,
        'BASE_RSI14_50': lambda x: base(x) and x['rsi14'] is not None and x['rsi14']>=50,
        'BASE_RSI14_55': lambda x: base(x) and x['rsi14'] is not None and x['rsi14']>=55,
        'BASE_CCI20_0': lambda x: base(x) and x['cci20'] is not None and x['cci20']>0,
        'BASE_CCI20_100': lambda x: base(x) and x['cci20'] is not None and x['cci20']>100,
        'BASE_RSI14_50_CCI0': lambda x: base(x) and x['rsi14'] is not None and x['rsi14']>=50 and x['cci20'] is not None and x['cci20']>0,
        'BASE_RSI14_55_CCI100': lambda x: base(x) and x['rsi14'] is not None and x['rsi14']>=55 and x['cci20'] is not None and x['cci20']>100,
        'BASE_RSI2_70_CCI100': lambda x: base(x) and x['rsi2']>=70 and x['cci20'] is not None and x['cci20']>100,
    }

    print('\n=== WILLIAMS V3 MOMENTUM FILTER COMPARISON USA ===')
    print('BASE=FIRST signal + 09:30-11:00 ET + volume>=1.5x prior10 mean + Williams CrossUp + RSI2>50')
    print('EXTRA_FILTERS=RSI2 thresholds, RSI14, CCI20')
    summaries=[]
    for name,fn in variants.items():
        rows=[x for x in allsig if fn(x)]; sm=summarize(name,rows); summaries.append(sm)
        print(name,'N=',sm['n'],'R5=',fmt(sm['r5']),'W5=',fmt(sm['w5']),'R10=',fmt(sm['r10']),'W10=',fmt(sm['w10']),'R20=',fmt(sm['r20']),'W20=',fmt(sm['w20']),'MFE=',fmt(sm['mfe']),'MAE=',fmt(sm['mae']))

    scored=[]
    for sm in summaries:
        if sm['n']<20: score=-999.0
        else:
            score=(sm['r5'] or 0)*0.20+(sm['r10'] or 0)*0.35+(sm['r20'] or 0)*0.45+((sm['mfe'] or 0)+(sm['mae'] or 0))*0.10
        scored.append((score,sm['name'],sm['n']))
    scored.sort(reverse=True)
    print('\n=== QUALITY RANK (N>=20) ===')
    for i,(sc,name,n) in enumerate(scored,1): print(i,name,'SCORE=',fmt(sc),'N=',n)

if __name__=='__main__': main()
