#!/usr/bin/env python3
import argparse, sqlite3, statistics
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n
   out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def mfe_mae(b,i,h=120):
 p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
 return (max(x['high'] for x in z)/p-1)*100,(min(x['low'] for x in z)/p-1)*100

events=[]
for (sym,d),b in G.items():
 if len(b)<40: continue
 c=[x['close'] for x in b]; rr=rsi(c); swings=[]
 for i in range(5,len(b)):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2=swings[-1]; s1=swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100; age=i-s2
  reclaim=(b[i]['close']/l2-1)*100
  if not (imp>=1 and hl>=-0.8 and b[i]['close']>b[i]['open'] and rr[i]>rr[i-1] and reclaim>=0.2): continue
  mfe,mae=mfe_mae(b,i)
  events.append(dict(sym=sym,d=d,imp=imp,hl=hl,age=age,mfe=mfe,mae=mae))

def is_base(e): return True
def is_hl(e): return 2<e['hl']<=5 and e['imp']>=2
def is_imp5(e): return e['imp']>=5 and e['age']<=2

def stat(es):
 if not es:return None
 return dict(n=len(es),avg=sum(e['mfe'] for e in es)/len(es),mae=sum(e['mae'] for e in es)/len(es),p3=sum(e['mfe']>=3 for e in es)/len(es)*100,p5=sum(e['mfe']>=5 for e in es)/len(es)*100,med=statistics.median(e['mfe'] for e in es))

def fmt(s):
 if not s:return 'N=0'
 return f"N={s['n']} MFE_AVG={s['avg']:.2f}% MED={s['med']:.2f}% MAE_AVG={s['mae']:.2f}% MFE>=3={s['p3']:.1f}% MFE>=5={s['p5']:.1f}%"

# rank symbols by BASE mean MFE only, then perform leave-top-k and leave-one-out on structural subsets
base_by=defaultdict(list)
for e in events: base_by[e['sym']].append(e)
rank=sorted(base_by, key=lambda s: sum(x['mfe'] for x in base_by[s])/len(base_by[s]), reverse=True)
print('=== WILLIAMS KOREA SUPPORT CONTEXT RANK ROBUST V66 ===')
print('Purpose: quantify whether structural edge survives dominant-symbol removal without retuning thresholds.')
print('BASE SYMBOL RANK:', ','.join(rank))
for label,pred in [('BASE',is_base),('HL_STRONG+IMP2',is_hl),('IMP5+FAST',is_imp5)]:
 print('\n---',label,'---')
 es=[e for e in events if pred(e)]
 print('ALL',fmt(stat(es)))
 for k in [1,2,3,4,5,6]:
  rm=set(rank[:k]); z=[e for e in es if e['sym'] not in rm]
  print(f'EX_TOP{k} {fmt(stat(z))}')
 print('LEAVE_ONE_OUT')
 for s in rank:
  z=[e for e in es if e['sym']!=s]
  q=stat(z)
  if q: print(f'  EX_{s} {fmt(q)}')
