#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where length(symbol)=6 and symbol glob '[0-9]*' order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9]*' order by symbol,trade_date,et_time",dates).fetchall()
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

def events_from(b,sym,d):
 c=[x['close'] for x in b]; rr=rsi(c); swings=[]; out=[]
 for i in range(5,len(b)-1):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  if not (b[i]['close']>b[i]['open'] and rr[i]>rr[i-1]): continue
  entry=b[i]['close']; support=l2
  # first support close break after entry
  br=None
  for k in range(i+1,min(len(b),i+121)):
   if b[k]['close']<support:
    br=k; break
  if br is None: continue
  # future +3 runner from entry within 120 bars
  t3=None
  for k in range(i+1,min(len(b),i+121)):
   if b[k]['high']>=entry*1.03:
    t3=k; break
  runner=t3 is not None
  # returns if exiting on break+delay bars, but if +3 is reached before delayed exit, flag it
  rec={'sym':sym,'d':d,'t':b[i]['et_time'],'runner':runner,'entry':entry,'br':br,'support':support}
  for delay in [0,1,2,3,5,10]:
   ex=min(br+delay,len(b)-1)
   rec[f'ret{delay}']=(b[ex]['close']/entry-1)*100
   rec[f'price{delay}']=b[ex]['close']
   rec[f'runner_saved{delay}']= bool(runner and t3 is not None and t3<=ex)
  out.append(rec)
 return out

events=[]
for (sym,d),b in G.items(): events += events_from(b,sym,d)

def stats(es,delay):
 if not es:return 'N=0'
 n=len(es); rets=[e[f'ret{delay}'] for e in es]; gp=sum(x for x in rets if x>0); gl=-sum(x for x in rets if x<0)
 pf=gp/gl if gl>0 else float('inf')
 saved=sum(e[f'runner_saved{delay}'] for e in es)
 runners=sum(e['runner'] for e in es)
 return f"N={n} WIN={sum(x>0 for x in rets)/n*100:.1f}% AVG_RET={sum(rets)/n:.3f}% PF={pf:.3f} RUNNER_SAVED_BEFORE_EXIT={saved}/{runners} ({(saved/runners*100 if runners else 0):.1f}%)"

print('=== WILLIAMS KOREA STRUCTURAL BREAK EXIT DELAY PROFILE V77 ===')
print('HL_STRONG+IMP2 contexts only. Compare immediate break exit against fixed 1/2/3/5/10-bar delays after first support close-break. No indicator veto.')
# chronological split by date
sd=sorted(set(e['d'] for e in events)); cut=max(1,int(len(sd)*0.5)); isd=set(sd[:cut]); oosd=set(sd[cut:])
for label,es in [('ALL',events),('IS',[e for e in events if e['d'] in isd]),('OOS',[e for e in events if e['d'] in oosd])]:
 print('\n---',label,'---')
 for delay in [0,1,2,3,5,10]: print(f'DELAY{delay}',stats(es,delay))
