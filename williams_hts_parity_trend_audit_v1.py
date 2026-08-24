#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
HORIZONS=(5,10,20,30,60)
TARGETS=(0.10,0.30,0.50,1.00)


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


def ema(vals,span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
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


def signal_rows(prev,cur):
    if len(prev)<100 or len(cur)<25:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    day_open=float(cur[0][1]); trigger=day_open+0.5*(ph-pl)
    highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]; vols=[float(r[5] or 0) for r in cur]
    r2=rsi(closes,2); c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    out=[]
    for i in range(2,len(cur)):
        # HTS parity formula: CROSSUP(C,B) AND RSI(2)>50
        if not (closes[i-1] <= trigger < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        prior=vols[max(0,i-10):i]; vavg=sum(prior)/len(prior) if prior else 0.0
        out.append({'i':i,'time':cur[i][0],'entry':closes[i],'trigger':trigger,'rsi2':r2[i],
                    'vol_ratio':(vols[i]/vavg if vavg>0 else None),
                    'cci':c20[i], 'cci_rising':bool(i>0 and c20[i] is not None and c20[i-1] is not None and c20[i]>c20[i-1]),
                    'hist':hist[i], 'hist_rising':bool(i>0 and hist[i]>hist[i-1]),
                    'macd_above_signal':bool(macd[i]>sig[i]),
                    'highs':highs,'lows':lows,'closes':closes})
    return out


def enrich(s):
    i=s['i']; entry=s['entry']; highs=s['highs']; lows=s['lows']; closes=s['closes']; n=len(closes)
    z=dict(s)
    for h in HORIZONS:
        j=min(n-1,i+h)
        z[f'mfe{h}']=pct(entry,max(highs[i:j+1]))
        z[f'mae{h}']=pct(entry,min(lows[i:j+1]))
        z[f'close{h}']=pct(entry,closes[j])
    # Which came first within 60m: +target or -0.30% adverse move?
    end=min(n-1,i+60)
    for t in TARGETS:
        hit=None
        for k in range(i,end+1):
            if pct(entry,lows[k]) <= -0.30:
                hit=False; break
            if pct(entry,highs[k]) >= t:
                hit=True; break
        z[f'target{t:.2f}_before_stop']=hit
    z['strong20']=z['mfe20']>=0.50
    z['strong60']=z['mfe60']>=1.00
    return z


def avg(xs): return statistics.fmean(xs) if xs else float('nan')

def report(label,rows):
    if not rows:
        print(label,'N=0'); return
    print('\n---',label,'N=',len(rows),'---')
    for h in HORIZONS:
        mf=[x[f'mfe{h}'] for x in rows]; ma=[x[f'mae{h}'] for x in rows]; cl=[x[f'close{h}'] for x in rows]
        print(f'H{h:02d} MFE_AVG={avg(mf):.4f}% MFE>0={100*sum(v>0 for v in mf)/len(mf):.2f}% '
              f'MFE>=0.30={100*sum(v>=0.30 for v in mf)/len(mf):.2f}% MFE>=0.50={100*sum(v>=0.50 for v in mf)/len(mf):.2f}% '
              f'CLOSE_POS={100*sum(v>0 for v in cl)/len(cl):.2f}% MAE_AVG={avg(ma):.4f}%')
    for t in TARGETS:
        vals=[x[f'target{t:.2f}_before_stop'] for x in rows if x[f'target{t:.2f}_before_stop'] is not None]
        print(f'TARGET +{t:.2f}% BEFORE -0.30% = {100*sum(vals)/len(vals):.2f}% N={len(vals)}' if vals else f'TARGET +{t:.2f}% BEFORE -0.30% = NA')
    print('STRONG20(MFE20>=0.50)=',f"{100*sum(x['strong20'] for x in rows)/len(rows):.2f}%")
    print('STRONG60(MFE60>=1.00)=',f"{100*sum(x['strong60'] for x in rows)/len(rows):.2f}%")


def feature_report(rows):
    print('\n=== STRONG-TREND FEATURE AUDIT ===')
    tests=[
        ('VOL>=1.5',lambda x:(x['vol_ratio'] or 0)>=1.5),
        ('CCI>100',lambda x:x['cci'] is not None and x['cci']>100),
        ('CCI_RISING',lambda x:x['cci_rising']),
        ('HIST_RISING',lambda x:x['hist_rising']),
        ('MACD>SIGNAL',lambda x:x['macd_above_signal']),
        ('VOL1.5+HIST_RISING',lambda x:(x['vol_ratio'] or 0)>=1.5 and x['hist_rising']),
        ('CCI100+HIST_RISING',lambda x:x['cci'] is not None and x['cci']>100 and x['hist_rising']),
        ('VOL1.5+CCI100+HIST_RISING',lambda x:(x['vol_ratio'] or 0)>=1.5 and x['cci'] is not None and x['cci']>100 and x['hist_rising']),
    ]
    for name,fn in tests:
        z=[x for x in rows if fn(x)]
        if not z: continue
        s20=100*sum(x['strong20'] for x in z)/len(z); s60=100*sum(x['strong60'] for x in z)/len(z)
        print(name,'N=',len(z),'STRONG20=',f'{s20:.2f}%','STRONG60=',f'{s60:.2f}%','MFE20=',f"{avg([x['mfe20'] for x in z]):.4f}%",'MAE20=',f"{avg([x['mae20'] for x in z]):.4f}%")


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); all_rows=[]; first_rows=[]
    for sym in syms:
        dm=load_days(con,sym,args.max_days); ds=sorted(dm); c=0
        for di in range(1,len(ds)):
            sigs=signal_rows(dm[ds[di-1]],dm[ds[di]])
            ez=[]
            for s in sigs:
                s['symbol']=sym; s['date']=ds[di]; ez.append(enrich(s))
            all_rows.extend(ez)
            if ez:first_rows.append(ez[0])
            c+=len(ez)
        print('AUDIT',sym,'DAYS=',max(0,len(ds)-1),'HTS_SIGNALS=',c)
    con.close()
    print('\n=== WILLIAMS HTS PARITY + TREND STRENGTH AUDIT V1 ===')
    print('RULE=CrossUp(close, day_open + 0.5*(prev_high-prev_low)) AND RSI(2)>50')
    print('NO morning/volume/CCI/MACD entry filters. ALL raw HTS-equivalent signals are measured.')
    report('ALL_SIGNALS',all_rows)
    report('FIRST_SIGNAL_PER_DAY',first_rows)
    feature_report(all_rows)
    print('\n=== FIRST 20 SIGNALS ===')
    for x in all_rows[:20]:
        print(x['symbol'],x['date'],x['time'],'ENTRY=',round(x['entry'],4),'RSI2=',round(x['rsi2'],2),
              'MFE5=',f"{x['mfe5']:.3f}",'MFE20=',f"{x['mfe20']:.3f}",'MFE60=',f"{x['mfe60']:.3f}",
              'MAE20=',f"{x['mae20']:.3f}",'VOLR=',('NA' if x['vol_ratio'] is None else f"{x['vol_ratio']:.2f}"),
              'CCI=',('NA' if x['cci'] is None else f"{x['cci']:.1f}"),'HIST_UP=',x['hist_rising'])

if __name__=='__main__': main()
