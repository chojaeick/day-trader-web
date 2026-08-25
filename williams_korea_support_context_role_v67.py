#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO KRX DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def path(b,i):
 p=b[i]['close']; z=b[i:]
 mfe=(max(x['high'] for x in z)/p-1)*100; mae=(min(x['low'] for x in z)/p-1)*100
 # time to +1/+3 and max adverse before +3
 t1=t3=None; pre3=0.0
 for k,x in enumerate(z):
  adv=(x['low']/p-1)*100; pre3=min(pre3,adv)
  if t1 is None and x['high']>=p*1.01:t1=k
  if t3 is None and x['high']>=p*1.03:t3=k; break
 return mfe,mae,t1,t3,pre3

e=[]
for (s,d),b in G.items():
 if len(b)<40: continue
 c=[x['close'] for x in b]; rr=rsi(c); swings=[]
 for i in range(5,len(b)):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2,s1=swings[-1],swings[-2]
  if i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100; rec=(b[i]['close']/l2-1)*100
  if not (imp>=2 and 2<hl<=5 and b[i]['close']>b[i]['open'] and rr[i]>rr[i-1] and rec>=0.2): continue
  mfe,mae,t1,t3,pre3=path(b,i)
  e.append((s,d,b[i]['et_time'],imp,hl,i-s2,mfe,mae,t1,t3,pre3))

print('=== WILLIAMS KOREA STRUCTURE ROLE AUDIT V67 ===')
print('Question: is HL_STRONG+IMP2 better used as HOLD/REARM context than as entry filter?')
print('N=',len(e))
if not e: raise SystemExit

def pct(cond): return sum(cond(x) for x in e)/len(e)*100
print(f"MFE>=1 {pct(lambda x:x[6]>=1):.1f}%  MFE>=3 {pct(lambda x:x[6]>=3):.1f}%  MFE>=5 {pct(lambda x:x[6]>=5):.1f}%")
print(f"MAE<=-1 {pct(lambda x:x[7]<=-1):.1f}%  MAE<=-2 {pct(lambda x:x[7]<=-2):.1f}%  MAE<=-3 {pct(lambda x:x[7]<=-3):.1f}%")
# For cases that eventually make +3, quantify how much drawdown they often need first.
r=[x for x in e if x[9] is not None]
print('RUNNERS_TO_3 N=',len(r))
if r:
 vals=sorted(x[10] for x in r)
 med=vals[len(vals)//2]
 print(f"PRE3_MAE_AVG={sum(vals)/len(vals):.2f}% MED={med:.2f}% <=-1={sum(v<=-1 for v in vals)/len(vals)*100:.1f}% <=-2={sum(v<=-2 for v in vals)/len(vals)*100:.1f}% <=-3={sum(v<=-3 for v in vals)/len(vals)*100:.1f}%")
 t3=[x[9] for x in r]; print(f"T3_BARS_AVG={sum(t3)/len(t3):.1f} MED={sorted(t3)[len(t3)//2]} <=10={sum(v<=10 for v in t3)/len(t3)*100:.1f}% <=30={sum(v<=30 for v in t3)/len(t3)*100:.1f}%")
print('--- SAMPLE RUNNERS WITH DEEP PULLBACK BEFORE +3 ---')
for x in sorted(r,key=lambda z:z[10])[:25]:
 print(f"{x[1]} {x[0]} T={x[2]} IMP={x[3]:.2f}% HL={x[4]:.2f}% AGE={x[5]} PRE3_MAE={x[10]:.2f}% T3={x[9]} MFE={x[6]:.2f}%")
