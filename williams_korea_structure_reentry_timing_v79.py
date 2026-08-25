#!/usr/bin/env python3
import argparse, sqlite3, math
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where length(symbol)=6 order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and length(symbol)=6 and session='REGULAR' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
 out=[None]*len(c); gs=[]; ls=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
  if i>=n:
   ag=sum(gs[i-n:i])/n; al=sum(ls[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def ema(v,n):
 out=[None]*len(v); k=2/(n+1); x=None
 for i,z in enumerate(v):
  x=z if x is None else z*k+x*(1-k); out[i]=x
 return out

def pfrets(rs):
 if not rs:return (0,0,0)
 gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0)
 pf=gp/gl if gl>0 else (999 if gp>0 else 0)
 return len(rs),sum(x>0 for x in rs)/len(rs)*100,sum(rs)/len(rs),pf

events=[]
for (sym,d),b in G.items():
 if len(b)<50:continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[e12[i]-e26[i] for i in range(len(c))]; sig=ema(mac,9); hist=[mac[i]-sig[i] for i in range(len(c))]
 swings=[]
 for i in range(5,len(b)-2):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)):swings.append(j)
  if len(swings)<2:continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2:continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1]); imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5):continue
  entry=i; sup=l2
  br=None
  for k in range(entry+1,min(len(b),entry+121)):
   if b[k]['close']<sup:br=k;break
  if br is None:continue
  exitp=b[br]['close']
  # first structural reclaim within 20 bars
  re=None
  for k in range(br+1,min(len(b),br+21)):
   if b[k]['close']>=sup:
    re=k;break
  if re is None:
   events.append(dict(sym=sym,d=d,re=False,base=(exitp/c[entry]-1)*100));continue
  # policies: reclaim close, +1 bar, +2 bars; and confirmation on reclaim
  def ret_at(k):
   end=min(len(b)-1,k+120); return (b[end]['close']/b[k]['close']-1)*100
  bull=b[re]['close']>b[re]['open']; rturn=rr[re] is not None and rr[re-1] is not None and rr[re]>rr[re-1]; mup=hist[re]>hist[re-1]
  item=dict(sym=sym,d=d,re=True,base=(exitp/c[entry]-1)*100,lag=re-br,bull=bull,rsi=rturn,macd=mup)
  for off in (0,1,2):
   k=min(len(b)-1,re+off); item[f'r{off}']=ret_at(k)
  events.append(item)

print('=== WILLIAMS KOREA STRUCTURE REENTRY TIMING V79 ===')
print('After structural break exit, compare re-entry timing on first support reclaim. No threshold retuning.')
valid=[e for e in events if e.get('re')]
print('EVENTS',len(events),'WITH_RECLAIM20',len(valid))
for name,flt in [
 ('RECLAIM_NOW',lambda e:True),
 ('RECLAIM+BULL',lambda e:e['bull']),
 ('RECLAIM+RSI_TURN',lambda e:e['rsi']),
 ('RECLAIM+MACD_UP',lambda e:e['macd']),
 ('RECLAIM+RSI+MACD',lambda e:e['rsi'] and e['macd'])]:
 z=[e for e in valid if flt(e)]
 print('\n---',name,'--- N=',len(z))
 for off in (0,1,2):
  rs=[e[f'r{off}'] for e in z]; n,w,av,pf=pfrets(rs); print(f'ENTRY_RECLAIM+{off} N={n} WIN={w:.1f}% AVG_RET={av:.3f}% PF={pf:.3f}')
 if z:
  print(f'RECLAIM_LAG AVG={sum(e["lag"] for e in z)/len(z):.1f} MED={sorted(e["lag"] for e in z)[len(z)//2]}')
# chronological split
z=valid; z=sorted(z,key=lambda e:(e['d'],e['sym'])); cut=len(z)//2
for label,q in [('IS',z[:cut]),('OOS',z[cut:])]:
 print('\n---',label,'RECLAIM_NOW ---')
 for off in (0,1,2):
  rs=[e[f'r{off}'] for e in q]; n,w,av,pf=pfrets(rs); print(f'+{off} N={n} WIN={w:.1f}% AVG_RET={av:.3f}% PF={pf:.3f}')
