#!/usr/bin/env python3
import argparse, sqlite3, statistics
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

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def metrics(es):
 if not es:return 'N=0'
 rs=[e['ret'] for e in es]; gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=gp/gl if gl>0 else float('inf')
 return f"N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<45: continue
 c=[x['close'] for x in b]; mh=macdh(c); rr=rsi(c)
 pv=[]; cv=0.0; vv=0.0
 for x in b:
  tp=(x['high']+x['low']+x['close'])/3; cv+=tp*x['volume']; vv+=x['volume']; pv.append(cv/vv if vv else x['close'])
 swings=[]
 for i in range(5,len(b)-4):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  br=None
  for q in range(i,min(len(b)-4,i+120)):
   if b[q]['close']<l2: br=q; break
  if br is None: continue
  rc=None
  for q in range(br+1,min(len(b)-4,br+21)):
   if b[q]['close']>=l2: rc=q; break
  if rc is None or mh[rc] is None or mh[rc-1] is None or not (mh[rc]>mh[rc-1]): continue
  reclaim=(b[rc]['close']/l2-1)*100; above_vwap=b[rc]['close']>=pv[rc]; rsi50=(rr[rc] is not None and rr[rc]>=50)
  ret=(b[-1]['close']/b[rc]['close']-1)*100
  events.append(dict(sym=sym,d=d,ret=ret,reclaim=reclaim,above_vwap=above_vwap,rsi50=rsi50))

all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); oos_dates=set(all_dates[cut:])
oos=[e for e in events if e['d'] in oos_dates]

def filt(es,name):
 if name=='BASE': return es
 if name=='VWAP': return [e for e in es if e['above_vwap']]
 if name=='FIRM_VWAP': return [e for e in es if e['above_vwap'] and e['reclaim']>=0.3]
 if name=='FIRM': return [e for e in es if e['reclaim']>=0.3]
 return []

print('=== WILLIAMS KOREA REENTRY RECOVERY STATE ROBUST V85 ===')
print('Freeze V84 recovery states. OOS robustness only: symbol-balanced, leave-one-out, and ex-dominant-symbol checks. No retuning.')
for name in ['BASE','VWAP','FIRM','FIRM_VWAP']:
 es=filt(oos,name)
 print(f'\n--- OOS {name} ---'); print(metrics(es))
 by=defaultdict(list)
 for e in es:by[e['sym']].append(e)
 if by:
  avgs=[]
  for s,z in by.items():
   rs=[x['ret'] for x in z]; gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=gp/gl if gl>0 else float('inf')
   avgs.append((s,len(z),sum(rs)/len(rs),sum(x>0 for x in rs)/len(rs)*100,pf,statistics.median(rs)))
  print(f"SYMBOL_BAL GROUPS={len(avgs)} AVG_RET={sum(x[2] for x in avgs)/len(avgs):.3f}% WIN={sum(x[3] for x in avgs)/len(avgs):.1f}% MED_AVG={sum(x[5] for x in avgs)/len(avgs):.3f}%")
  for x in sorted(avgs,key=lambda y:y[2],reverse=True): print(f"  {x[0]} N={x[1]} AVG={x[2]:.3f}% WIN={x[3]:.1f}% PF={x[4]:.3f} MED={x[5]:.3f}%")

# robustness focus: FIRM+VWAP
es=filt(oos,'FIRM_VWAP')
syms=sorted(set(e['sym'] for e in es))
print('\n--- OOS FIRM_VWAP LEAVE-ONE-OUT ---')
for s in syms:
 z=[e for e in es if e['sym']!=s]; print('EX_'+s,metrics(z))
for ex in [('950260',),('950260','950160'),('950260','950160','080220','466100')]:
 z=[e for e in es if e['sym'] not in ex]; print('EX_'+'_'.join(ex),metrics(z))
