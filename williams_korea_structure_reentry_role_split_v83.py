#!/usr/bin/env python3
import argparse, sqlite3, math, statistics
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO KRX DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(x,n):
 out=[]; k=2/(n+1); v=None
 for z in x:
  v=z if v is None else z*k+v*(1-k); out.append(v)
 return out

def macdh(c):
 e12,e26=ema(c,12),ema(c,26); m=[x-y for x,y in zip(e12,e26)]; s=ema(m,9); return [x-y for x,y in zip(m,s)]

def metrics(rs):
 if not rs:return 'N=0'
 wins=sum(x>0 for x in rs); gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=(gp/gl if gl>0 else float('inf'))
 return f"N={len(rs)} WIN={wins/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<45: continue
 c=[x['close'] for x in b]; mh=macdh(c)
 # causal confirmed swing lows, same structural family as prior audits
 swings=[]
 for i in range(5,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  # first close break after context point
  br=None
  for j2 in range(i,min(len(b)-3,i+120)):
   if b[j2]['close']<l2: br=j2; break
  if br is None: continue
  # first reclaim within 20 bars
  rc=None
  for j2 in range(br+1,min(len(b)-3,br+21)):
   if b[j2]['close']>=l2: rc=j2; break
  if rc is None: continue
  for delay in (0,1,2):
   e=rc+delay
   if e>=len(b): continue
   macd_ok=(mh[rc] is not None and mh[rc-1] is not None and mh[rc]>mh[rc-1])
   if not macd_ok: continue
   ret=(b[-1]['close']/b[e]['close']-1)*100
   events.append(dict(sym=sym,d=d,ret=ret,delay=delay,imp=imp,hl=hl,lag=rc-br))

print('=== WILLIAMS KOREA REENTRY ROLE SPLIT V83 ===')
print('Purpose: after V82, decide whether structural reentry is a general rule or should be enabled only for strong/leader symbols. No threshold optimization.')
for delay in (0,1,2):
 es=[e for e in events if e['delay']==delay]
 print(f'\n--- MACD+{delay} ALL ---'); print(metrics([e['ret'] for e in es]))
 by=defaultdict(list)
 for e in es: by[e['sym']].append(e['ret'])
 strong=[]; weak=[]
 for s,rs in by.items():
  avg=sum(rs)/len(rs)
  (strong if avg>0 else weak).extend(rs)
 print('POSITIVE_SYMBOLS',metrics(strong))
 print('NONPOSITIVE_SYMBOLS',metrics(weak))
 print('SYMBOL DETAIL')
 for s,rs in sorted(by.items(), key=lambda kv: sum(kv[1])/len(kv[1]), reverse=True):
  print(s, metrics(rs))

# fixed leader proxy from same-day realized structural opportunity: classify symbols with average event return >0 on first half dates, test second half only.
all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); is_dates=set(all_dates[:cut]); oos_dates=set(all_dates[cut:])
for delay in (0,1,2):
 is_by=defaultdict(list)
 for e in events:
  if e['delay']==delay and e['d'] in is_dates: is_by[e['sym']].append(e['ret'])
 leaders={s for s,rs in is_by.items() if len(rs)>=2 and sum(rs)/len(rs)>0}
 oos=[e['ret'] for e in events if e['delay']==delay and e['d'] in oos_dates]
 oos_lead=[e['ret'] for e in events if e['delay']==delay and e['d'] in oos_dates and e['sym'] in leaders]
 oos_non=[e['ret'] for e in events if e['delay']==delay and e['d'] in oos_dates and e['sym'] not in leaders]
 print(f'\n--- FROZEN IS-LEADER SPLIT MACD+{delay} ---')
 print('IS_LEADERS',','.join(sorted(leaders)) if leaders else 'NONE')
 print('OOS_ALL',metrics(oos)); print('OOS_IS_LEADERS',metrics(oos_lead)); print('OOS_NONLEADERS',metrics(oos_non))
