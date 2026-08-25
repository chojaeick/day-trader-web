#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9]*' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def future_mfe(b,i,h=120):
 p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
 return (max(x['high'] for x in z)/p-1)*100

def bars_to_level(b,i,pct):
 p=b[i]['close']; tgt=p*(1+pct/100)
 for j in range(i,min(len(b),i+121)):
  if b[j]['high']>=tgt:return j-i
 return None

events=[]
for (sym,d),b in G.items():
 if len(b)<40: continue
 c=[x['close'] for x in b]; rr=rsi(c); swings=[]
 for i in range(5,len(b)):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (2<hl<=5 and imp>=2 and b[i]['close']>b[i]['open'] and rr[i]>rr[i-1]): continue
  mfe=future_mfe(b,i); t3=bars_to_level(b,i,3)
  # structural exit candidates relative to defended swing low s2
  support=l2
  breaks={}
  for tol in [0.0,0.3,0.5,0.8,1.0]:
   hit=None
   for j2 in range(i,min(len(b),i+121)):
    if b[j2]['close'] < support*(1-tol/100): hit=j2-i; break
   breaks[tol]=hit
  # two-close confirmation below support
  two=None
  for j2 in range(i+1,min(len(b),i+121)):
   if b[j2-1]['close']<support and b[j2]['close']<support: two=j2-i; break
  events.append(dict(sym=sym,d=d,t=b[i]['et_time'],mfe=mfe,t3=t3,breaks=breaks,two=two))

print('=== WILLIAMS KOREA STRUCTURAL BREAK EXIT AUDIT V68 ===')
print('Role test: for HL_STRONG+IMP2 contexts, how often would a prior-swing-low break kill future +3% runners?')
print('N=',len(events))
runners=[e for e in events if e['t3'] is not None]
print('RUNNERS_TO_3 N=',len(runners))
for tol in [0.0,0.3,0.5,0.8,1.0]:
 allhit=[e for e in events if e['breaks'][tol] is not None]
 rhit=[e for e in runners if e['breaks'][tol] is not None and e['breaks'][tol] < e['t3']]
 print(f"CLOSE_BREAK_{tol:.1f}% ALL_HIT={len(allhit)}/{len(events)} ({len(allhit)/len(events)*100 if events else 0:.1f}%) RUNNER_KILLED_BEFORE3={len(rhit)}/{len(runners)} ({len(rhit)/len(runners)*100 if runners else 0:.1f}%)")
alltwo=[e for e in events if e['two'] is not None]
rkill=[e for e in runners if e['two'] is not None and e['two']<e['t3']]
print(f"TWO_CLOSE_BELOW_SUPPORT ALL_HIT={len(alltwo)}/{len(events)} ({len(alltwo)/len(events)*100 if events else 0:.1f}%) RUNNER_KILLED_BEFORE3={len(rkill)}/{len(runners)} ({len(rkill)/len(runners)*100 if runners else 0:.1f}%)")
print('--- RUNNERS THAT BREAK SUPPORT BEFORE +3 ---')
for e in sorted(runners,key=lambda x:(x['breaks'][0.5] is None, x['breaks'][0.5] if x['breaks'][0.5] is not None else 999))[:30]:
 if e['breaks'][0.0] is None and e['breaks'][0.5] is None: continue
 print(e['d'],e['sym'],e['t'],f"T3={e['t3']} B0={e['breaks'][0.0]} B03={e['breaks'][0.3]} B05={e['breaks'][0.5]} B08={e['breaks'][0.8]} TWO={e['two']} MFE={e['mfe']:.2f}%")
