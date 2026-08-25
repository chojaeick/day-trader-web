#!/usr/bin/env python3
import argparse, sqlite3, re
from collections import defaultdict

KR=re.compile(r'^\d{6}$')
P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
# Korea-only: V63 mixed US/KR symbols, so re-check the same structural hypothesis on 6-digit KRX symbols only.
syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol") if KR.match(str(r[0] or ''))]
G=defaultdict(list)
for s in syms:
 ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,a.max_days))]
 if not ds: continue
 qs=','.join('?'*len(ds))
 rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date in ({qs}) and interval_min=1 order by trade_date,et_time",[s]+ds).fetchall()
 for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); gains=[0.0]*len(c); losses=[0.0]*len(c)
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gains[i]=max(d,0); losses[i]=max(-d,0)
 for i in range(n,len(c)):
  ag=sum(gains[i-n+1:i+1])/n; al=sum(losses[i-n+1:i+1])/n
  out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def mfe_mae(b,i,h=120):
 p=float(b[i]['close']); z=b[i:min(len(b),i+h+1)]
 return (max(float(x['high']) for x in z)/p-1)*100,(min(float(x['low']) for x in z)/p-1)*100

events=[]
for (s,d),b in G.items():
 if len(b)<40: continue
 c=[float(x['close']) for x in b]; rr=rsi(c); swings=[]
 for i in range(5,len(b)):
  j=i-2
  if float(b[j]['low'])<=min(float(b[k]['low']) for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i-s2<0 or i-s2>10: continue
  l1=float(b[s1]['low']); l2=float(b[s2]['low']); hi=max(float(x['high']) for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100; reclaim=(float(b[i]['close'])/l2-1)*100; age=i-s2
  bullish=float(b[i]['close'])>float(b[i]['open']); rturn=rr[i]>rr[i-1]
  if not (imp>=1 and hl>=-0.8 and bullish and rturn and reclaim>=0.2): continue
  mfe,mae=mfe_mae(b,i)
  events.append(dict(s=s,d=d,imp=imp,hl=hl,age=age,mfe=mfe,mae=mae))

def filt(e,name):
 if name=='ALL': return True
 if name=='HL0.5_5+IMP2+FAST': return .5<e['hl']<=5 and e['imp']>=2 and e['age']<=2
 if name=='HL_STRONG+IMP2': return 2<e['hl']<=5 and e['imp']>=2
 if name=='IMP2+FAST': return e['imp']>=2 and e['age']<=2
 if name=='IMP5+FAST': return e['imp']>=5 and e['age']<=2
 return False

def metrics(es):
 n=len(es)
 if not n:return (0,0,0,0,0,0)
 return n,sum(x['mfe'] for x in es)/n,sum(x['mae'] for x in es)/n,100*sum(x['mfe']>=1 for x in es)/n,100*sum(x['mfe']>=3 for x in es)/n,100*sum(x['mfe']>=5 for x in es)/n

def balanced(es,key):
 g=defaultdict(list)
 for e in es:g[e[key]].append(e)
 vals=[]
 for k,z in g.items(): vals.append((k,metrics(z)))
 return vals

print('=== WILLIAMS KOREA SUPPORT CONTEXT MARKET SPLIT V64 ===')
print('KRX 6-digit symbols only. Re-check V63 structural edge without US symbols.')
for name in ['ALL','HL0.5_5+IMP2+FAST','HL_STRONG+IMP2','IMP2+FAST','IMP5+FAST']:
 es=[e for e in events if filt(e,name)]; m=metrics(es)
 print(f"\n--- {name} ---")
 print(f"POOLED N={m[0]} MFE_AVG={m[1]:.2f}% MAE_AVG={m[2]:.2f}% MFE>=1={m[3]:.1f}% MFE>=3={m[4]:.1f}% MFE>=5={m[5]:.1f}%")
 for key,label in [('s','SYMBOL'),('d','DAY')]:
  v=balanced(es,key)
  if not v: print(label+'_BALANCED N=0'); continue
  print(f"{label}_BALANCED GROUPS={len(v)} MFE_AVG={sum(x[1][1] for x in v)/len(v):.2f}% MFE>=3={sum(x[1][4] for x in v)/len(v):.1f}% MFE>=5={sum(x[1][5] for x in v)/len(v):.1f}%")
  for k,mm in sorted(v,key=lambda x:x[1][1],reverse=True)[:20]: print(f"  {k} N={mm[0]} MFE_AVG={mm[1]:.2f}% MFE>=3={mm[4]:.1f}% MFE>=5={mm[5]:.1f}%")
