#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); gains=[]; losses=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
  if i>=n:
   ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n
   out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def find_t3(b,i):
 p=b[i]['close']
 for k in range(i+1,len(b)):
  if b[k]['high']>=p*1.03:return k-i
 return None

def hit_before(b,i,t3,support,pct):
 end=i+t3 if t3 is not None else len(b)-1
 for k in range(i+1,min(end+1,len(b))):
  if b[k]['close']<support*(1-pct): return k
 return None

def reclaim_after(b,hit,support,window):
 if hit is None:return None
 for k in range(hit+1,min(len(b),hit+window+1)):
  if b[k]['close']>=support:return k-hit
 return None

events=[]
for (sym,d),b in G.items():
 if len(b)<40:continue
 c=[x['close'] for x in b]; rr=rsi(c); swings=[]
 for i in range(5,len(b)):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)):swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None:continue
  s2,s1=swings[-1],swings[-2]
  if i-s2>10 or s2<=s1:continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5 and b[i]['close']>b[i]['open'] and rr[i]>rr[i-1]):continue
  t3=find_t3(b,i)
  events.append((sym,d,i,l2,t3,b))

print('=== WILLIAMS KOREA STRUCTURE BREAK + RECLAIM AUDIT V69 ===')
print('Question: if prior swing-low breaks, can fast reclaim distinguish temporary undercut from true failure?')
runners=[e for e in events if e[4] is not None]
print('N=',len(events),' RUNNERS_TO_3=',len(runners))
for pct in [0.0,0.005,0.01]:
 hit=[]
 for e in runners:
  sym,d,i,sup,t3,b=e; h=hit_before(b,i,t3,sup,pct)
  if h is not None:hit.append((e,h))
 print(f'\nBREAK_{pct*100:.1f}% RUNNER_BREAKS={len(hit)}/{len(runners)} ({(100*len(hit)/len(runners) if runners else 0):.1f}%)')
 for w in [1,2,3,5,10]:
  rc=sum(reclaim_after(e[0][5],e[1],e[0][3],w) is not None for e in hit)
  print(f'  RECLAIM<={w} BARS {rc}/{len(hit)} ({(100*rc/len(hit) if hit else 0):.1f}%)')
# Compare non-runners: break then reclaim vs break-no-reclaim
non=[e for e in events if e[4] is None]
for pct in [0.0,0.005,0.01]:
 br=[]
 for e in non:
  sym,d,i,sup,t3,b=e; h=hit_before(b,i,None,sup,pct)
  if h is not None:br.append((e,h))
 print(f'\nNONRUNNER BREAK_{pct*100:.1f}% {len(br)}/{len(non)}')
 for w in [2,3,5,10]:
  rc=sum(reclaim_after(e[0][5],e[1],e[0][3],w) is not None for e in br)
  print(f'  RECLAIM<={w} BARS {rc}/{len(br)} ({(100*rc/len(br) if br else 0):.1f}%)')
