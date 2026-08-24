import argparse, sqlite3, math, statistics
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']


def rsi2(closes):
    if len(closes) < 3: return None
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0.0)); losses.append(max(-d,0.0))
    if len(gains)<2: return None
    g=sum(gains[-2:])/2.0; l=sum(losses[-2:])/2.0
    if l==0: return 100.0
    rs=g/l
    return 100.0-(100.0/(1.0+rs))


def pct(a,b):
    return None if a in (None,0) or b is None else (b/a-1.0)*100.0


def avg(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None


def win(xs):
    xs=[x for x in xs if x is not None]
    return 100.0*sum(1 for x in xs if x>0)/len(xs) if xs else None


def fmt(x):
    return 'NA' if x is None else f'{x:.4f}'


def load_symbol(con,symbol,max_days):
    days=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    days=sorted(days)
    out={}
    for d in days:
        rows=con.execute("select ts,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and session='REGULAR' order by ts",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out


def generate_signals(symbol,daymap):
    dates=sorted(daymap)
    sig=[]
    for di in range(1,len(dates)):
        prev=daymap[dates[di-1]]; cur=daymap[dates[di]]
        if len(prev)<10 or len(cur)<25: continue
        prev_hi=max(float(r[2]) for r in prev); prev_lo=min(float(r[3]) for r in prev)
        day_open=float(cur[0][1]); trigger=day_open+0.5*(prev_hi-prev_lo)
        closes=[]; vols=[]; seen=False
        for i,r in enumerate(cur):
            ts,o,h,l,c,v=r; c=float(c); v=float(v or 0); closes.append(c); vols.append(v)
            if i<2: continue
            r2=rsi2(closes)
            cross=float(cur[i-1][4]) <= trigger and c > trigger
            if not cross or r2 is None or r2<=50: continue
            # rolling volume confirmation: current > 1.5x mean of prior 10 bars
            prior=vols[max(0,i-10):i]
            vol_avg=sum(prior)/len(prior) if prior else 0
            vol_ok=bool(vol_avg>0 and v>=1.5*vol_avg)
            # infer local ET time from ts string; DB ts is ET-like timestamp in these caches
            s=str(ts)
            hhmm=None
            try:
                if 'T' in s:
                    t=s.split('T',1)[1][:5].replace(':',''); hhmm=int(t)
                elif len(s)>=12:
                    hhmm=int(s[-6:-2])
            except: hhmm=None
            morning=bool(hhmm is not None and 930 <= hhmm <= 1100)
            horizons={}
            for n in (1,3,5,10,20):
                horizons[n]=pct(c,float(cur[i+n][4])) if i+n<len(cur) else None
            future=cur[i:min(len(cur),i+21)]
            mfe=max((float(x[2])/c-1)*100 for x in future) if future else None
            mae=min((float(x[3])/c-1)*100 for x in future) if future else None
            sig.append({'symbol':symbol,'date':dates[di],'ts':ts,'entry':c,'trigger':trigger,'rsi2':r2,'first':not seen,'morning':morning,'vol_ok':vol_ok,'r':horizons,'mfe20':mfe,'mae20':mae})
            seen=True
    return sig


def summarize(name, rows):
    vals={n:[x['r'][n] for x in rows if x['r'][n] is not None] for n in (1,3,5,10,20)}
    return {
        'name':name,'n':len(rows),
        **{f'r{n}':avg(vals[n]) for n in vals},
        **{f'w{n}':win(vals[n]) for n in vals},
        'mfe':avg([x['mfe20'] for x in rows]),
        'mae':avg([x['mae20'] for x in rows]),
    }


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    symbols=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db)
    allsig=[]
    for s in symbols:
        dm=load_symbol(con,s,args.max_days)
        ss=generate_signals(s,dm); allsig.extend(ss)
        print('AUDIT',s,'DAYS=',max(0,len(dm)-1),'SIGNALS=',len(ss))
    con.close()

    variants={
        'BASE': lambda x: True,
        'FIRST_ONLY': lambda x: x['first'],
        'MORNING_ONLY': lambda x: x['morning'],
        'RSI70': lambda x: x['rsi2']>=70,
        'FIRST_MORNING': lambda x: x['first'] and x['morning'],
        'FIRST_MORNING_VOLUME': lambda x: x['first'] and x['morning'] and x['vol_ok'],
    }
    print('\n=== WILLIAMS V2 FILTER COMPARISON USA ===')
    print('BASE_RULE=CrossUp(close, day_open+0.5*prev_day_range) AND RSI(2)>50')
    print('MORNING=09:30-11:00 ET  VOLUME=current_vol >= 1.5x prior10 mean')
    summaries=[]
    for name,fn in variants.items():
        rows=[x for x in allsig if fn(x)]; sm=summarize(name,rows); summaries.append(sm)
        print(name,'N=',sm['n'],'R5=',fmt(sm['r5']),'W5=',fmt(sm['w5']),'R10=',fmt(sm['r10']),'W10=',fmt(sm['w10']),'R20=',fmt(sm['r20']),'W20=',fmt(sm['w20']),'MFE=',fmt(sm['mfe']),'MAE=',fmt(sm['mae']))

    # quality rank: favor 5/10/20m expectancy, penalize adverse excursion, require sample size
    scored=[]
    for sm in summaries:
        if sm['n']<10: score=-999
        else:
            score=(sm['r5'] or 0)*0.25+(sm['r10'] or 0)*0.30+(sm['r20'] or 0)*0.45+((sm['mfe'] or 0)+(sm['mae'] or 0))*0.10
        scored.append((score,sm['name'],sm['n']))
    scored.sort(reverse=True)
    print('\n=== QUALITY RANK ===')
    for i,(sc,name,n) in enumerate(scored,1): print(i,name,'SCORE=',fmt(sc),'N=',n)

if __name__=='__main__': main()
