#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(v,n):
 out=[]; k=2/(n+1); e=None
 for x in v:
  e=x if e is None else x*k+e*(1-k); out.append(e)
 return out

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def pf(xs):
 gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); return gp/gl if gl>0 else float('inf')

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
 swings=[]
 for i in range(5,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)):
   swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1]); imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  support=l2
  br=None
  for k in range(max(i,s2+1),min(len(b),i+80)):
   if b[k]['close']<support: br=k; break
  if br is None: continue
  rec=None
  for k in range(br+1,min(len(b),br+21)):
   if b[k]['close']>=support: rec=k; break
  if rec is None: continue
  macdup = rec>0 and hist[rec]>hist[rec-1]
  rsiup = rec>0 and rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1]
  for lag in (0,1,2):
   ei=rec+lag
   if ei>=len(b): continue
   ret=(b[-1]['close']/b[ei]['close']-1)*100
   events.append(dict(sym=sym,d=d,lag=lag,ret=ret,macd=macdup,rsi=rsiup))

def report(es,label):
 xs=[e['ret'] for e in es]
 if not xs: print(label,'N=0'); return
 print(f"{label} N={len(xs)} WIN={sum(x>0 for x in xs)/len(xs)*100:.1f}% AVG_RET={sum(xs)/len(xs):.3f}% PF={pf(xs):.3f}")

print('=== WILLIAMS KOREA STRUCTURE REENTRY EX950260 V82 ===')
print('Purpose: verify whether V81 reentry edge survives without dominant symbol 950260; no retuning.')
for filt,name in [(lambda e:True,'NOW'),(lambda e:e['macd'],'MACD'),(lambda e:e['rsi'] and e['macd'],'RSI_MACD')]:
 for lag in (0,1,2):
  base=[e for e in events if e['lag']==lag and filt(e)]
  report(base,f'ALL {name}+{lag}')
  report([e for e in base if e['sym']!='950260'],f'EX950260 {name}+{lag}')
print('--- EX950260 SYMBOL DETAIL: MACD+2 ---')
by=defaultdict(list)
for e in events:
 if e['lag']==2 and e['macd'] and e['sym']!='950260': by[e['sym']].append(e)
for s,z in sorted(by.items(), key=lambda kv: sum(e['ret'] for e in kv[1])/len(kv[1]), reverse=True):
 report(z,s)
