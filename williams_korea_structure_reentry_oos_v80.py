#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(vals,n):
 out=[None]*len(vals); alpha=2/(n+1); e=None
 for i,v in enumerate(vals):
  e=v if e is None else alpha*v+(1-alpha)*e; out[i]=e
 return out

def rsi(vals,n=14):
 out=[None]*len(vals); gains=[]; losses=[]
 for i in range(1,len(vals)):
  d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
  if i>=n:
   ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n
   out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def pf(rs):
 g=sum(x for x in rs if x>0); l=-sum(x for x in rs if x<0)
 return float('inf') if l==0 and g>0 else (g/l if l else 0)

def stats(name,rs):
 if not rs:return f"{name} N=0"
 return f"{name} N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf(rs):.3f}"

events=[]
for (sym,d),b in G.items():
 if len(b)<60: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
 swings=[]
 for i in range(6,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s2=swings[-1]; s1=swings[-2]
  if not (0<=i-s2<=10): continue
  l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1]); imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  # first close break of second swing low after structural context
  br=None
  for k in range(i+1,min(len(b),i+121)):
   if b[k]['close']<l2: br=k; break
  if br is None: continue
  rec=None
  for k in range(br+1,min(len(b),br+21)):
   if b[k]['close']>=l2: rec=k; break
  if rec is None: continue
  # de-duplicate nearby identical break/reclaim events
  key=(sym,d,br,rec)
  events.append((key,b,rr,hist,rec))

# unique by exact break/reclaim
U={e[0]:e for e in events}; events=list(U.values())
# chronological split by date
sd=sorted(set(e[0][1] for e in events)); cut=max(1,int(len(sd)*0.5)); isd=set(sd[:cut]); oosd=set(sd[cut:])

def evaluate(sub):
 out=defaultdict(list)
 for key,b,rr,hist,rec in sub:
  for lag in (0,1,2):
   en=rec+lag
   if en>=len(b): continue
   # evaluate to end-of-day; diagnostic only
   ret=(b[-1]['close']/b[en]['close']-1)*100
   out[f'NOW+{lag}'].append(ret)
   if rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1]: out[f'RSI+{lag}'].append(ret)
   if hist[rec]>hist[rec-1]: out[f'MACD+{lag}'].append(ret)
   if rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1] and hist[rec]>hist[rec-1]: out[f'RSI_MACD+{lag}'].append(ret)
 return out

print('=== WILLIAMS KOREA STRUCTURE REENTRY OOS V80 ===')
print('Freeze V79 candidates; compare ALL/IS/OOS without retuning. Return measured to EOD from reclaim+0/+1/+2.')
for label,sub in [('ALL',events),('IS',[e for e in events if e[0][1] in isd]),('OOS',[e for e in events if e[0][1] in oosd])]:
 print('\n---',label,'EVENTS=',len(sub),'---')
 o=evaluate(sub)
 for n in ['NOW+0','NOW+1','NOW+2','RSI+0','RSI+1','RSI+2','MACD+0','MACD+1','MACD+2','RSI_MACD+0','RSI_MACD+1','RSI_MACD+2']:
  print(stats(n,o.get(n,[])))
