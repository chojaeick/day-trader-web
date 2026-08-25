#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where length(symbol)=6 order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(v,n):
 out=[]; k=2/(n+1); x=None
 for z in v:
  x=z if x is None else z*k+x*(1-k); out.append(x)
 return out

def macdh(c):
 e12,e26=ema(c,12),ema(c,26); m=[x-y for x,y in zip(e12,e26)]; s=ema(m,9); return [x-y for x,y in zip(m,s)]

def build_events():
 ev=[]
 for (sym,d),b in G.items():
  if len(b)<50: continue
  c=[x['close'] for x in b]; mh=macdh(c); swings=[]
  for i in range(5,len(b)-1):
   j=i-2
   if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
   if len(swings)<2: continue
   s2,s1=swings[-1],swings[-2]
   if s2<=s1 or i<s2 or i-s2>10: continue
   l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
   imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
   if not (imp>=2 and 2<hl<=5): continue
   # require bullish reclaim from local pullback structure
   if not (b[i]['close']>b[i]['open'] and b[i]['close']>=l2*1.002): continue
   entry=i; support=l2
   # simulate three policies from same context
   for policy in ('IMMEDIATE_BREAK','WAIT2_RECLAIM','WAIT2_RECLAIM_MACD'):
    exit_i=None; reason='TIMEOUT'
    k=entry+1
    while k<len(b):
     if b[k]['close']<support:
      if policy=='IMMEDIATE_BREAK': exit_i=k; reason='BREAK'; break
      end=min(len(b)-1,k+2); veto=False
      for q in range(k+1,end+1):
       rec=b[q]['close']>=support
       macdup=mh[q]>mh[q-1]
       if rec and (policy=='WAIT2_RECLAIM' or macdup):
        veto=True; k=q; break
      if not veto:
       exit_i=end; reason='BREAK_FAIL'; break
     k+=1
     if k-entry>=120: break
    if exit_i is None: exit_i=min(len(b)-1,entry+120); reason='TIMEOUT'
    ret=(b[exit_i]['close']/b[entry]['close']-1)*100
    mfe=(max(x['high'] for x in b[entry:exit_i+1])/b[entry]['close']-1)*100
    ev.append(dict(sym=sym,d=d,policy=policy,ret=ret,mfe=mfe,reason=reason))
 return ev

ev=build_events(); ds=sorted(set(e['d'] for e in ev)); cut=max(1,len(ds)//2); isd=set(ds[:cut]); oosd=set(ds[cut:])

def summ(z):
 if not z:return 'N=0'
 n=len(z); avg=sum(x['ret'] for x in z)/n; win=sum(x['ret']>0 for x in z)/n*100; pf=sum(max(x['ret'],0) for x in z)/max(1e-9,sum(max(-x['ret'],0) for x in z)); m=sum(x['mfe'] for x in z)/n
 return f"N={n} WIN={win:.1f}% AVG_RET={avg:.3f}% PF={pf:.3f} MFE_AVG={m:.2f}%"

print('=== WILLIAMS KOREA HOLD/EXIT STATE MACHINE OOS V76 ===')
print('Same HL_STRONG+IMP2 contexts. Compare immediate support-break exit vs 2-bar reclaim grace, with/without MACD confirmation.')
for split,name in [(set(ds),'ALL'),(isd,'IS'),(oosd,'OOS')]:
 print('\n---',name,'---')
 for p in ('IMMEDIATE_BREAK','WAIT2_RECLAIM','WAIT2_RECLAIM_MACD'):
  z=[e for e in ev if e['d'] in split and e['policy']==p]; print(p, summ(z))
  by=defaultdict(list)
  for e in z:by[e['reason']].append(e)
  for r,zz in sorted(by.items()): print(' ',r,summ(zz))
