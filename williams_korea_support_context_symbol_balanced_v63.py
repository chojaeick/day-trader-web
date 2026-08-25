#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
# V63 deliberately re-audits V62 contexts but reports symbol-balanced/day-balanced stats.
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' order by symbol,trade_date,et_time",dates).fetchall()
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

def mfe_mae(b,i,h=120):
 p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
 return (max(x['high'] for x in z)/p-1)*100,(min(x['low'] for x in z)/p-1)*100

events=[]
for (sym,d),b in G.items():
 if len(b)<40: continue
 c=[x['close'] for x in b]; rr=rsi(c)
 # causal local swing lows: confirmed using only current/past bars
 swings=[]
 for i in range(5,len(b)):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)):
   swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2=swings[-1]; s1=swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  impulse=(hi/l1-1)*100; hl=(l2/l1-1)*100
  reclaim=(b[i]['close']/l2-1)*100; age=i-s2
  bullish=b[i]['close']>b[i]['open']; rturn=rr[i]>rr[i-1]
  if not (impulse>=1 and hl>=-0.8 and bullish and rturn and reclaim>=0.2): continue
  m,x=mfe_mae(b,i)
  events.append(dict(sym=sym,d=d,t=b[i]['et_time'],imp=impulse,hl=hl,age=age,mfe=m,mae=x))

def ok(e,name):
 if name=='ALL': return True
 if name=='HL0.5_5+IMP2+FAST': return .5<e['hl']<=5 and e['imp']>=2 and e['age']<=2
 if name=='HL_STRONG+IMP2': return 2<e['hl']<=5 and e['imp']>=2
 if name=='IMP2+FAST': return e['imp']>=2 and e['age']<=2
 if name=='IMP5+FAST': return e['imp']>=5 and e['age']<=2
 return False

def summarize(es):
 if not es:return 'N=0'
 n=len(es); return f"N={n} MFE_AVG={sum(e['mfe'] for e in es)/n:.2f}% MAE_AVG={sum(e['mae'] for e in es)/n:.2f}% MFE>=1={sum(e['mfe']>=1 for e in es)/n*100:.1f}% MFE>=3={sum(e['mfe']>=3 for e in es)/n*100:.1f}% MFE>=5={sum(e['mfe']>=5 for e in es)/n*100:.1f}%"

def balanced(es,key):
 groups=defaultdict(list)
 for e in es:groups[e[key]].append(e)
 vals=[]
 for k,z in groups.items():
  vals.append((k,len(z),sum(e['mfe']>=1 for e in z)/len(z)*100,sum(e['mfe']>=3 for e in z)/len(z)*100,sum(e['mfe']>=5 for e in z)/len(z)*100,sum(e['mfe'] for e in z)/len(z)))
 return vals

print('=== WILLIAMS KOREA SUPPORT CONTEXT SYMBOL-BALANCED V63 ===')
print('V62 robustness check: prevent one explosive symbol/day from dominating pooled event statistics.')
for name in ['ALL','HL0.5_5+IMP2+FAST','HL_STRONG+IMP2','IMP2+FAST','IMP5+FAST']:
 es=[e for e in events if ok(e,name)]
 print('\n---',name,'POOLED ---'); print(summarize(es))
 for key,label in [('sym','SYMBOL'),('d','DAY')]:
  v=balanced(es,key)
  if not v: print(label+'_BALANCED N=0'); continue
  print(f"{label}_BALANCED GROUPS={len(v)} MFE_AVG={sum(x[5] for x in v)/len(v):.2f}% MFE>=1={sum(x[2] for x in v)/len(v):.1f}% MFE>=3={sum(x[3] for x in v)/len(v):.1f}% MFE>=5={sum(x[4] for x in v)/len(v):.1f}%")
  for x in sorted(v,key=lambda q:q[5],reverse=True)[:12]: print(f"  {x[0]} N={x[1]} MFE_AVG={x[5]:.2f}% MFE>=3={x[3]:.1f}% MFE>=5={x[4]:.1f}%")
