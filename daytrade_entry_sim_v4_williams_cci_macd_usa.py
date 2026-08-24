#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','TSLA','PLTR']


def pct(a,b): return None if not a else (b/a-1.0)*100.0

def avg(xs):
    xs=[x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None

def win(xs):
    xs=[x for x in xs if x is not None]
    return 100.0*sum(x>0 for x in xs)/len(xs) if xs else None

def fmt(x): return 'NA' if x is None else f'{x:.4f}'

def ema(vals, span):
    if not vals: return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out

def rsi(vals, period=14):
    out=[None]*len(vals)
    if len(vals)<period+1: return out
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(period+1,len(vals)):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def rsi2(vals): return rsi(vals,2)

def cci20(highs,lows,closes):
    tp=[(h+l+c)/3.0 for h,l,c in zip(highs,lows,closes)]
    out=[None]*len(tp)
    for i in range(19,len(tp)):
        w=tp[i-19:i+1]; ma=sum(w)/20.0; md=sum(abs(x-ma) for x in w)/20.0
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def load_symbol(con,symbol,max_days):
    days=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    days=sorted(days); out={}
    for d in days:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out

def hhmm_of(et):
    s=str(et)
    try:
        if 'T' in s: return int(s.split('T',1)[1][:5].replace(':',''))
        if ':' in s: return int(s[:5].replace(':',''))
        if s.isdigit() and len(s)>=12: return int(s[8:12])
        if s.isdigit() and len(s)>=4: return int(s[:4])
    except: return None
    return None

def generate(symbol,dm):
    dates=sorted(dm); events=[]
    for di in range(1,len(dates)):
        prev=dm[dates[di-1]]; cur=dm[dates[di]]
        if len(prev)<100 or len(cur)<40: continue
        prev_hi=max(float(x[2]) for x in prev); prev_lo=min(float(x[3]) for x in prev)
        day_open=float(cur[0][1]); trigger=day_open+0.5*(prev_hi-prev_lo)
        highs=[float(x[2]) for x in cur]; lows=[float(x[3]) for x in cur]; closes=[float(x[4]) for x in cur]; vols=[float(x[5] or 0) for x in cur]
        r2=rsi2(closes); r14=rsi(closes,14); cci=cci20(highs,lows,closes)
        e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
        first_seen=False
        for i in range(20,len(cur)):
            cross=closes[i-1] <= trigger and closes[i] > trigger
            if not cross or r2[i] is None or r2[i] <= 50: continue
            prior=vols[max(0,i-10):i]; vavg=sum(prior)/len(prior) if prior else 0
            vol_ok=bool(vavg>0 and vols[i]>=1.5*vavg)
            hhmm=hhmm_of(cur[i][0]); morning=bool(hhmm is not None and 930<=hhmm<=1100)
            first=not first_seen; first_seen=True
            e={'symbol':symbol,'date':dates[di],'time':str(cur[i][0]),'entry':closes[i],'first':first,'morning':morning,'vol_ok':vol_ok,
               'rsi2':r2[i],'rsi14':r14[i],'cci':cci[i],'cci_rising':bool(i>=2 and cci[i] is not None and cci[i-1] is not None and cci[i-2] is not None and cci[i]>cci[i-1]>cci[i-2]),
               'hist':hist[i],'hist_pos':hist[i]>0,'hist_rising':bool(i>=2 and hist[i]>hist[i-1]>hist[i-2])}
            for n in (5,10,20): e[f'r{n}']=pct(closes[i],closes[i+n]) if i+n<len(cur) else None
            fut=cur[i:min(len(cur),i+21)]
            e['mfe']=max((float(x[2])/closes[i]-1)*100 for x in fut) if fut else None
            e['mae']=min((float(x[3])/closes[i]-1)*100 for x in fut) if fut else None
            events.append(e)
    return events

def summarize(name, rows):
    return {'name':name,'n':len(rows),'r5':avg([x['r5'] for x in rows]),'w5':win([x['r5'] for x in rows]),'r10':avg([x['r10'] for x in rows]),'w10':win([x['r10'] for x in rows]),'r20':avg([x['r20'] for x in rows]),'w20':win([x['r20'] for x in rows]),'mfe':avg([x['mfe'] for x in rows]),'mae':avg([x['mae'] for x in rows])}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    con=sqlite3.connect(args.db); allsig=[]
    for s in [x.strip().upper() for x in args.symbols.split(',') if x.strip()]:
        dm=load_symbol(con,s,args.max_days); ss=generate(s,dm); allsig.extend(ss); print('AUDIT',s,'DAYS=',max(0,len(dm)-1),'SIGNALS=',len(ss))
    con.close()
    base=lambda x: x['first'] and x['morning'] and x['vol_ok']
    base_rows=[x for x in allsig if base(x)]
    cvals=sorted([x['cci'] for x in base_rows if x['cci'] is not None])
    print('\n=== WILLIAMS V4 CCI/MACD FILTER COMPARISON USA ===')
    print('BASE=FIRST + 09:30-11:00 ET + volume>=1.5x prior10 + Williams CrossUp + RSI2>50')
    if cvals:
        print('CCI_AUDIT N=',len(cvals),'MIN=',fmt(cvals[0]),'P25=',fmt(cvals[len(cvals)//4]),'MED=',fmt(cvals[len(cvals)//2]),'P75=',fmt(cvals[(len(cvals)*3)//4]),'MAX=',fmt(cvals[-1]))
    variants={
      'BASE':lambda x: True,
      'CCI50':lambda x:x['cci'] is not None and x['cci']>50,
      'CCI100':lambda x:x['cci'] is not None and x['cci']>100,
      'CCI150':lambda x:x['cci'] is not None and x['cci']>150,
      'CCI_RISING':lambda x:x['cci_rising'],
      'MACD_HIST_POS':lambda x:x['hist_pos'],
      'MACD_HIST_RISING':lambda x:x['hist_rising'],
      'CCI100_HIST_POS':lambda x:x['cci'] is not None and x['cci']>100 and x['hist_pos'],
      'CCI100_HIST_RISING':lambda x:x['cci'] is not None and x['cci']>100 and x['hist_rising'],
      'CCI_RISING_HIST_RISING':lambda x:x['cci_rising'] and x['hist_rising'],
      'CCI100_HIST_POS_RSI14_55':lambda x:x['cci'] is not None and x['cci']>100 and x['hist_pos'] and x['rsi14'] is not None and x['rsi14']>55,
    }
    summaries=[]
    for name,fn in variants.items():
        rows=[x for x in base_rows if fn(x)]; sm=summarize(name,rows); summaries.append(sm)
        print(name,'N=',sm['n'],'R5=',fmt(sm['r5']),'W5=',fmt(sm['w5']),'R10=',fmt(sm['r10']),'W10=',fmt(sm['w10']),'R20=',fmt(sm['r20']),'W20=',fmt(sm['w20']),'MFE=',fmt(sm['mfe']),'MAE=',fmt(sm['mae']))
    scored=[]
    for sm in summaries:
        if sm['n']<20: score=-999
        else: score=(sm['r5'] or 0)*.2+(sm['r10'] or 0)*.35+(sm['r20'] or 0)*.45+((sm['mfe'] or 0)+(sm['mae'] or 0))*.1
        scored.append((score,sm['name'],sm['n']))
    scored.sort(reverse=True)
    print('\n=== QUALITY RANK (N>=20) ===')
    for i,(sc,nm,n) in enumerate(scored,1): print(i,nm,'SCORE=',fmt(sc),'N=',n)

if __name__=='__main__': main()
