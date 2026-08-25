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

def metrics(rs):
 if not rs:return 'N=0'
 gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=(gp/gl if gl>0 else float('inf'))
 return f"N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<60: continue
 c=[x['close'] for x in b]; mh=macdh(c)
 day_open=b[0]['open']
 # causal confirmed swing lows
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
  br=None
  for j2 in range(i,min(len(b)-3,i+120)):
   if b[j2]['close']<l2: br=j2; break
  if br is None: continue
  rc=None
  for j2 in range(br+1,min(len(b)-3,br+21)):
   if b[j2]['close']>=l2: rc=j2; break
  if rc is None or rc<1: continue
  macd_ok=(mh[rc]>mh[rc-1])
  if not macd_ok: continue
  e=rc
  ret=(b[-1]['close']/b[e]['close']-1)*100
  # causal leader-state features at reclaim
  tod=(b[e]['close']/day_open-1)*100
  look=max(0,e-30)
  local_high=max(x['high'] for x in b[look:e+1])
  from_local_high=(b[e]['close']/local_high-1)*100
  vol20=sum(x['volume'] for x in b[max(0,e-19):e+1])/max(1,min(20,e+1))
  vr=b[e]['volume']/vol20 if vol20>0 else 0
  # percentile rank of current close inside last 30 bars
  closes=[x['close'] for x in b[look:e+1]]
  rank=sum(x<=b[e]['close'] for x in closes)/len(closes)*100
  events.append(dict(sym=sym,d=d,ret=ret,tod=tod,imp=imp,hl=hl,lag=rc-br,fh=from_local_high,vr=vr,rank=rank))

all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); oos_dates=set(all_dates[cut:])
oos=[e for e in events if e['d'] in oos_dates]
print('=== WILLIAMS KOREA REENTRY LEADER-STATE V87 ===')
print('Goal: explain 950260-style success using causal same-day strength state, not symbol identity. Frozen reentry = support reclaim + MACD hist up.')
print('OOS_BASE',metrics([e['ret'] for e in oos]))
checks=[
 ('TOD>=3',[e for e in oos if e['tod']>=3]),
 ('TOD>=5',[e for e in oos if e['tod']>=5]),
 ('IMP>=5',[e for e in oos if e['imp']>=5]),
 ('RANK30>=70',[e for e in oos if e['rank']>=70]),
 ('WITHIN3%_30BAR_HIGH',[e for e in oos if e['fh']>=-3]),
 ('VOLR>=1',[e for e in oos if e['vr']>=1]),
 ('TOD3+RANK70',[e for e in oos if e['tod']>=3 and e['rank']>=70]),
 ('TOD3+NEARHIGH',[e for e in oos if e['tod']>=3 and e['fh']>=-3]),
 ('IMP5+NEARHIGH',[e for e in oos if e['imp']>=5 and e['fh']>=-3]),
]
for name,es in checks: print(name,metrics([e['ret'] for e in es]))
print('--- EX950260 ---')
ex=[e for e in oos if e['sym']!='950260']
print('BASE',metrics([e['ret'] for e in ex]))
for name,_ in checks:
 if name=='TOD>=3': es=[e for e in ex if e['tod']>=3]
 elif name=='TOD>=5': es=[e for e in ex if e['tod']>=5]
 elif name=='IMP>=5': es=[e for e in ex if e['imp']>=5]
 elif name=='RANK30>=70': es=[e for e in ex if e['rank']>=70]
 elif name=='WITHIN3%_30BAR_HIGH': es=[e for e in ex if e['fh']>=-3]
 elif name=='VOLR>=1': es=[e for e in ex if e['vr']>=1]
 elif name=='TOD3+RANK70': es=[e for e in ex if e['tod']>=3 and e['rank']>=70]
 elif name=='TOD3+NEARHIGH': es=[e for e in ex if e['tod']>=3 and e['fh']>=-3]
 else: es=[e for e in ex if e['imp']>=5 and e['fh']>=-3]
 print(name,metrics([e['ret'] for e in es]))
print('--- SYMBOL DETAIL OOS ---')
by=defaultdict(list)
for e in oos:by[e['sym']].append(e)
for s,es in sorted(by.items()):
 print(s,metrics([e['ret'] for e in es]),f"TOD_AVG={sum(e['tod'] for e in es)/len(es):.2f}% IMP_AVG={sum(e['imp'] for e in es)/len(es):.2f}% RANK30={sum(e['rank'] for e in es)/len(es):.1f}")
