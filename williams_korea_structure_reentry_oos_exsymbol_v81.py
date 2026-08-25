#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by trade_date,symbol,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(v,n):
 out=[]; x=None; k=2/(n+1)
 for z in v:
  x=z if x is None else z*k+x*(1-k); out.append(x)
 return out

def rsi(c,n=14):
 out=[None]*len(c); g=[]; l=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def pf(rs):
 gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0)
 return float('inf') if gl==0 and gp>0 else (gp/gl if gl else 0.0)

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
 swings=[]
 for i in range(6,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)):
   swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  # first structural close-break after context
  br=None
  for k in range(max(i,s2+1),min(len(b)-3,i+120)):
   if b[k]['close']<l2: br=k; break
  if br is None: continue
  # first reclaim within 20 bars
  rc=None
  for k in range(br+1,min(len(b)-3,br+21)):
   if b[k]['close']>=l2: rc=k; break
  if rc is None: continue
  rturn = rr[rc] is not None and rr[rc-1] is not None and rr[rc]>rr[rc-1]
  mup = hist[rc]>hist[rc-1]
  # Return to EOD for +0/+1/+2. V80-style diagnostic robustness, not a trading engine.
  for tag,cond in [('NOW',True),('RSI',rturn),('MACD',mup),('RSI_MACD',rturn and mup)]:
   if not cond: continue
   for lag in (0,1,2):
    en=rc+lag
    if en>=len(b): continue
    ret=(b[-1]['close']/b[en]['close']-1)*100
    events.append(dict(sym=sym,d=d,tag=tag,lag=lag,ret=ret))

all_dates=sorted(set(e['d'] for e in events)); cut=all_dates[len(all_dates)//2] if all_dates else ''

def stats(es):
 rs=[e['ret'] for e in es]
 if not rs:return 'N=0'
 return f"N={len(rs)} WIN={sum(x>0 for x in rs)/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf(rs):.3f}"

print('=== WILLIAMS KOREA STRUCTURE REENTRY OOS EX-SYMBOL V81 ===')
print('Freeze V80 candidates. Test whether OOS edge survives removal of dominant symbols; no retuning.')
oos=[e for e in events if e['d']>=cut]
for tag in ('NOW','MACD','RSI_MACD'):
 for lag in (0,1,2):
  es=[e for e in oos if e['tag']==tag and e['lag']==lag]
  print(f'OOS {tag}+{lag} {stats(es)}')
print('--- OOS LEAVE-ONE-SYMBOL-OUT: MACD+2 ---')
base=[e for e in oos if e['tag']=='MACD' and e['lag']==2]
syms=sorted(set(e['sym'] for e in base))
for s in syms:
 print('EX_'+s,stats([e for e in base if e['sym']!=s]))
print('--- OOS SYMBOL DETAIL: MACD+2 ---')
for s in syms:
 print(s,stats([e for e in base if e['sym']==s]))
