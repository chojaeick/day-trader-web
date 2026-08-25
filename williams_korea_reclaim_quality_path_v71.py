#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 and symbol glob '[0-9]*' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(x,n):
 out=[]; k=2/(n+1); v=None
 for z in x:
  v=z if v is None else z*k+v*(1-k); out.append(v)
 return out

def rsi(c,n=14):
 out=[None]*len(c); gains=[]; losses=[]
 for i in range(1,len(c)):
  d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
  if i>=n:
   ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n
   out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def metrics(b,i,h=120):
 p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
 mfe=(max(x['high'] for x in z)/p-1)*100; mae=(min(x['low'] for x in z)/p-1)*100
 t3=next((j for j,x in enumerate(z) if x['high']>=p*1.03),None)
 return mfe,mae,t3

ev=[]
for (sym,d),b in G.items():
 if len(b)<60: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
 swings=[]
 for i in range(5,len(b)-1):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  # require actual support break after context, then measure first reclaim within 10 bars
  br=None
  for j in range(i,min(len(b),i+121)):
   if b[j]['close']<l2: br=j; break
  if br is None: continue
  rec=None
  for j in range(br+1,min(len(b),br+11)):
   if b[j]['close']>=l2: rec=j; break
  mfe0,mae0,t30=metrics(b,i)
  runner=t30 is not None
  if rec is None:
   ev.append(dict(sym=sym,d=d,runner=runner,rec=99,bull=0,rturn=0,mup=0,score=0,mfe=mfe0,mae=mae0,t3=t30))
   continue
  bull=int(b[rec]['close']>b[rec]['open'])
  rturn=int(rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1])
  mup=int(hist[rec]>hist[rec-1])
  score=bull+rturn+mup
  ev.append(dict(sym=sym,d=d,runner=runner,rec=rec-br,bull=bull,rturn=rturn,mup=mup,score=score,mfe=mfe0,mae=mae0,t3=t30))

print('=== WILLIAMS KOREA RECLAIM QUALITY PATH V71 ===')
print('HL_STRONG+IMP2 contexts with an actual support break. First reclaim within 10 bars; no exit simulation.')
print('N=',len(ev),'RUNNER=',sum(e['runner'] for e in ev),'NONRUNNER=',sum(not e['runner'] for e in ev))

def line(name,z):
 if not z: print(name,'N=0'); return
 n=len(z); r=sum(e['runner'] for e in z)
 print(f"{name} N={n} RUNNER={r}/{n} ({r/n*100:.1f}%) MFE_AVG={sum(e['mfe'] for e in z)/n:.2f}% MAE_AVG={sum(e['mae'] for e in z)/n:.2f}%")

line('NO_RECLAIM<=10',[e for e in ev if e['rec']==99])
for k in [1,2,3,5,10]: line(f'RECLAIM<={k}',[e for e in ev if e['rec']<=k])
for s in [0,1,2,3]: line(f'RECLAIM<=2_SCORE>={s}',[e for e in ev if e['rec']<=2 and e['score']>=s])
line('RECLAIM<=2_RSI_ONLY',[e for e in ev if e['rec']<=2 and e['rturn']])
line('RECLAIM<=2_RSI+MACD',[e for e in ev if e['rec']<=2 and e['rturn'] and e['mup']])
print('--- SYMBOL DETAIL FOR RECLAIM<=2_SCORE>=2 ---')
by=defaultdict(list)
for e in ev:
 if e['rec']<=2 and e['score']>=2: by[e['sym']].append(e)
for sym,z in sorted(by.items(),key=lambda kv:len(kv[1]),reverse=True):
 n=len(z); r=sum(e['runner'] for e in z); print(f"{sym} N={n} RUNNER={r}/{n} ({r/n*100:.1f}%)")
