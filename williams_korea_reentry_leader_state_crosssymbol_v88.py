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

def stats(es):
 if not es:return 'N=0'
 rs=[e['ret'] for e in es]; gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=gp/gl if gl>0 else float('inf')
 return f"N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<45: continue
 c=[x['close'] for x in b]; mh=macdh(c); op=b[0]['open']
 swings=[]
 for i in range(5,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s1,s2=swings[-2],swings[-1]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  br=None
  for k in range(i,min(len(b)-3,i+120)):
   if b[k]['close']<l2: br=k; break
  if br is None: continue
  rc=None
  for k in range(br+1,min(len(b)-3,br+21)):
   if b[k]['close']>=l2: rc=k; break
  if rc is None or rc<1: continue
  if not (mh[rc]>mh[rc-1]): continue
  win=b[max(0,rc-29):rc+1]
  hi30=max(x['high'] for x in win); lo30=min(x['low'] for x in win)
  rank30=100*(b[rc]['close']-lo30)/(hi30-lo30) if hi30>lo30 else 50.0
  tod=(b[rc]['close']/op-1)*100
  ret=(b[-1]['close']/b[rc]['close']-1)*100
  events.append(dict(sym=sym,d=d,ret=ret,tod=tod,imp=imp,rank30=rank30))

all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); oos_dates=set(all_dates[cut:])
oos=[e for e in events if e['d'] in oos_dates]
ex=[e for e in oos if e['sym']!='950260']
print('=== WILLIAMS KOREA REENTRY LEADER-STATE CROSS-SYMBOL V88 ===')
print('Freeze V87 features. Test broad strength buckets and symbol-balanced performance without 950260; no optimization.')
print('OOS_ALL',stats(oos)); print('OOS_EX950260',stats(ex))
conds=[('TOD_GE0',lambda e:e['tod']>=0),('TOD_GE3',lambda e:e['tod']>=3),('TOD_GE5',lambda e:e['tod']>=5),('TOD_GE3_RANK70',lambda e:e['tod']>=3 and e['rank30']>=70),('TOD_GE3_IMP5',lambda e:e['tod']>=3 and e['imp']>=5),('RANK70',lambda e:e['rank30']>=70)]
for name,fn in conds:
 z=[e for e in ex if fn(e)]
 print('\n'+name,stats(z))
 by=defaultdict(list)
 for e in z:by[e['sym']].append(e)
 if by:
  av=[]
  for s,es in by.items(): av.append((s,sum(x['ret'] for x in es)/len(es),len(es),sum(x['ret']>0 for x in es)/len(es)*100))
  print(f"SYMBOL_BAL GROUPS={len(av)} AVG_OF_AVG={sum(x[1] for x in av)/len(av):.3f}% AVG_WIN={sum(x[3] for x in av)/len(av):.1f}%")
  for s,a1,n,w in sorted(av,key=lambda x:x[1],reverse=True): print(f"  {s} N={n} AVG={a1:.3f}% WIN={w:.1f}%")
