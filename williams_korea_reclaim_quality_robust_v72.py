#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(vals,n):
 out=[]; alpha=2/(n+1); e=None
 for v in vals:
  e=v if e is None else alpha*v+(1-alpha)*e; out.append(e)
 return out

def rsi(c,n=14):
 out=[None]*len(c); gains=[]; losses=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
  if i>=n:
   ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def pct(a,b): return (a/b-1)*100 if b else 0

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
 swings=[]
 for i in range(6,len(b)-1):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2=swings[-1]; s1=swings[-2]
  if s2<=s1 or not (0<=i-s2<=10): continue
  l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1]); imp=pct(hi,l1); hl=pct(l2,l1)
  if not (imp>=2 and 2<hl<=5): continue
  # require actual post-context support break
  br=None
  for k in range(i,min(len(b),i+121)):
   if b[k]['close']<l2: br=k; break
  if br is None: continue
  rec=None
  for k in range(br+1,min(len(b),br+11)):
   if b[k]['close']>=l2: rec=k; break
  future=b[i:min(len(b),i+121)]
  mfe=max(x['high'] for x in future)/b[i]['close']-1
  runner=mfe>=0.03
  if rec is None:
   events.append(dict(sym=sym,d=d,runner=runner,recbars=99,score=-1,rsi=False,macd=False,bull=False)); continue
  bull=b[rec]['close']>b[rec]['open']; rt=rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1]; mh=hist[rec]>hist[rec-1]
  score=int(bull)+int(rt)+int(mh)
  events.append(dict(sym=sym,d=d,runner=runner,recbars=rec-br,score=score,rsi=rt,macd=mh,bull=bull))

print('=== WILLIAMS KOREA RECLAIM QUALITY ROBUST V72 ===')
print('KRX only. Robustness of reclaim quality; no retuning, no exit simulation.')
print('N=',len(events),' RUNNER=',sum(e['runner'] for e in events))

def stat(es,label):
 if not es: print(label,'N=0'); return
 n=len(es); r=sum(e['runner'] for e in es)
 print(f'{label} N={n} RUNNER={r}/{n} ({r/n*100:.1f}%)')

conds=[
 ('NO_RECLAIM<=10',lambda e:e['recbars']==99),
 ('RECLAIM<=2',lambda e:e['recbars']<=2),
 ('RECLAIM<=2+RSI',lambda e:e['recbars']<=2 and e['rsi']),
 ('RECLAIM<=2+RSI+MACD',lambda e:e['recbars']<=2 and e['rsi'] and e['macd']),
 ('RECLAIM<=2+SCORE3',lambda e:e['recbars']<=2 and e['score']>=3),
]
for name,f in conds:
 es=[e for e in events if f(e)]; stat(es,name)
 print('  SYMBOL_BREAKDOWN')
 by=defaultdict(list)
 for e in es: by[e['sym']].append(e)
 for s,z in sorted(by.items(), key=lambda kv:(-len(kv[1]),kv[0])):
  print(f"    {s} N={len(z)} RUNNER={sum(x['runner'] for x in z)/len(z)*100:.1f}%")

# Leave-one-out for the most promising fixed condition: <=2 + RSI + MACD
base=[e for e in events if e['recbars']<=2 and e['rsi'] and e['macd']]
print('--- LEAVE ONE SYMBOL OUT: RECLAIM<=2+RSI+MACD ---')
for s in sorted({e['sym'] for e in base}):
 z=[e for e in base if e['sym']!=s]
 if z: print(f"EX_{s} N={len(z)} RUNNER={sum(x['runner'] for x in z)/len(z)*100:.1f}%")
