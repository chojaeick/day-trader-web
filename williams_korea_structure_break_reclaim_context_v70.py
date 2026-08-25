#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); gains=[]; losses=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
  if i>=n:
   ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def ema(v,n):
 out=[]; k=2/(n+1); e=None
 for x in v:
  e=x if e is None else x*k+e*(1-k); out.append(e)
 return out

def macd_hist(c):
 e12=ema(c,12); e26=ema(c,26); m=[x-y for x,y in zip(e12,e26)]; s=ema(m,9); return [x-y for x,y in zip(m,s)]

def pct(a,b): return (a/b-1)*100 if b else 0

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); mh=macd_hist(c)
 swings=[]
 for i in range(5,len(b)-1):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or not (0<=i-s2<=10): continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=pct(hi,l1); hl=pct(l2,l1)
  if not (imp>=2 and 2<hl<=5): continue
  # bullish reclaim context event
  if not (b[i]['close']>b[i]['open'] and rr[i]>rr[i-1]): continue
  p=b[i]['close']; future=b[i:min(len(b),i+121)]
  mfe=max(x['high'] for x in future)/p*100-100
  runner=mfe>=3
  # first close break of prior support (s1 low), then reclaim above it
  br=None
  for k in range(i+1,min(len(b),i+121)):
   if b[k]['close']<l1:
    br=k; break
  if br is None: continue
  rec=None
  for k in range(br+1,min(len(b),br+11)):
   if b[k]['close']>=l1:
    rec=k; break
  # context at break/reclaim
  kctx=rec if rec is not None else br
  bull=b[kctx]['close']>b[kctx]['open']
  rturn=(rr[kctx] is not None and rr[kctx-1] is not None and rr[kctx]>rr[kctx-1])
  mturn=(kctx>0 and mh[kctx]>mh[kctx-1])
  # volume expansion vs trailing 20 excluding current
  prev=[b[x]['volume'] or 0 for x in range(max(0,kctx-20),kctx)]
  vavg=sum(prev)/len(prev) if prev else 0
  vratio=(b[kctx]['volume'] or 0)/vavg if vavg>0 else 0
  recbars=None if rec is None else rec-br
  events.append(dict(sym=sym,d=d,runner=runner,recbars=recbars,bull=bull,rturn=rturn,mturn=mturn,vr=vratio))

print('=== WILLIAMS KOREA BREAK/RECLAIM CONTEXT V70 ===')
print('HL_STRONG+IMP2 only. Compare runner vs nonrunner after actual support break.')
print('N=',len(events),'RUNNER=',sum(e['runner'] for e in events),'NONRUNNER=',sum(not e['runner'] for e in events))

def report(name,fn):
 z=[e for e in events if fn(e)];
 if not z: print(name,'N=0'); return
 r=sum(e['runner'] for e in z); print(f"{name} N={len(z)} RUNNER={r}/{len(z)} ({r/len(z)*100:.1f}%)")

for n in [1,2,3,5,10]: report(f'RECLAIM<={n}',lambda e,n=n:e['recbars'] is not None and e['recbars']<=n)
report('NO_RECLAIM<=10',lambda e:e['recbars'] is None)
report('RECLAIM<=2+BULL',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['bull'])
report('RECLAIM<=2+RSI_TURN',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['rturn'])
report('RECLAIM<=2+MACD_HIST_UP',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['mturn'])
report('RECLAIM<=2+BULL+RSI',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['bull'] and e['rturn'])
report('RECLAIM<=2+BULL+RSI+MACD',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['bull'] and e['rturn'] and e['mturn'])
report('RECLAIM<=2+VOL>=1',lambda e:e['recbars'] is not None and e['recbars']<=2 and e['vr']>=1)
