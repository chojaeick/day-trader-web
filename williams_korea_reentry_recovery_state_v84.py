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

def rsi14(c):
 out=[None]*len(c)
 if len(c)<15:return out
 gains=[]; losses=[]
 for i in range(1,15):
  d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
 ag=sum(gains)/14; al=sum(losses)/14
 out[14]=100 if al==0 else 100-(100/(1+ag/al))
 for i in range(15,len(c)):
  d=c[i]-c[i-1]; g=max(d,0); l=max(-d,0)
  ag=(ag*13+g)/14; al=(al*13+l)/14
  out[i]=100 if al==0 else 100-(100/(1+ag/al))
 return out

def vwap_series(b):
 out=[]; pv=0.0; vv=0.0
 for x in b:
  v=float(x['volume'] or 0); tp=(x['high']+x['low']+x['close'])/3
  pv+=tp*v; vv+=v; out.append(pv/vv if vv>0 else x['close'])
 return out

def metrics(es, key='ret'):
 rs=[e[key] for e in es if e.get(key) is not None]
 if not rs:return 'N=0'
 wins=sum(x>0 for x in rs); gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=(gp/gl if gl>0 else float('inf'))
 return f"N={len(rs)} WIN={wins/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<45: continue
 c=[x['close'] for x in b]; mh=macdh(c); r14=rsi14(c); vw=vwap_series(b)
 swings=[]
 for i in range(5,len(b)-5):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  br=None
  for j2 in range(i,min(len(b)-5,i+120)):
   if b[j2]['close']<l2: br=j2; break
  if br is None: continue
  rc=None
  for j2 in range(br+1,min(len(b)-5,br+21)):
   if b[j2]['close']>=l2: rc=j2; break
  if rc is None or rc<1: continue
  macd_ok=(mh[rc] is not None and mh[rc-1] is not None and mh[rc]>mh[rc-1])
  if not macd_ok: continue
  reclaim=(b[rc]['close']/l2-1)*100
  rvwap=(b[rc]['close']/vw[rc]-1)*100 if vw[rc] else 0.0
  r=r14[rc] if r14[rc] is not None else 50.0
  lag=rc-br
  ret=(b[-1]['close']/b[rc]['close']-1)*100
  # 3-bar delayed confirmation: all lows remain above support and close advances from reclaim.
  k=min(rc+3,len(b)-1)
  hold_support=all(b[z]['low']>=l2 for z in range(rc+1,k+1)) if k>rc else False
  advance=(b[k]['close']>b[rc]['close']) if k>rc else False
  confirm3=hold_support and advance
  ret3=(b[-1]['close']/b[k]['close']-1)*100 if confirm3 else None
  events.append(dict(sym=sym,d=d,ret=ret,ret3=ret3,reclaim=reclaim,rvwap=rvwap,rsi=r,lag=lag,confirm3=confirm3))

all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); is_dates=set(all_dates[:cut]); oos_dates=set(all_dates[cut:])

def show(label, pred, key='ret'):
 es=[e for e in events if pred(e)]
 print(label, metrics(es,key))
 return es

print('=== WILLIAMS KOREA REENTRY RECOVERY STATE V84 ===')
print('Base is frozen MACD-up reclaim entry from V83. Audit recovery state only; no symbol whitelist and no threshold optimization.')
print('EVENTS',len(events),'IS_DATES',','.join(sorted(is_dates)),'OOS_DATES',','.join(sorted(oos_dates)))
print('\n--- ALL RECOVERY STATES ---')
show('BASE_MACD_RECLAIM',lambda e:True)
show('FAST_RECLAIM<=2',lambda e:e['lag']<=2)
show('FIRM_RECLAIM>=0.3',lambda e:e['reclaim']>=0.3)
show('ABOVE_VWAP',lambda e:e['rvwap']>=0)
show('RSI>=50',lambda e:e['rsi']>=50)
show('FIRM+VWAP',lambda e:e['reclaim']>=0.3 and e['rvwap']>=0)
show('FIRM+RSI',lambda e:e['reclaim']>=0.3 and e['rsi']>=50)
show('VWAP+RSI',lambda e:e['rvwap']>=0 and e['rsi']>=50)
show('FIRM+VWAP+RSI',lambda e:e['reclaim']>=0.3 and e['rvwap']>=0 and e['rsi']>=50)
show('CONFIRM3_SUPPORT+ADVANCE',lambda e:e['confirm3'],key='ret3')

print('\n--- OOS RECOVERY STATES ---')
show('OOS_BASE',lambda e:e['d'] in oos_dates)
show('OOS_FAST<=2',lambda e:e['d'] in oos_dates and e['lag']<=2)
show('OOS_FIRM>=0.3',lambda e:e['d'] in oos_dates and e['reclaim']>=0.3)
show('OOS_ABOVE_VWAP',lambda e:e['d'] in oos_dates and e['rvwap']>=0)
show('OOS_RSI>=50',lambda e:e['d'] in oos_dates and e['rsi']>=50)
show('OOS_FIRM+VWAP',lambda e:e['d'] in oos_dates and e['reclaim']>=0.3 and e['rvwap']>=0)
show('OOS_VWAP+RSI',lambda e:e['d'] in oos_dates and e['rvwap']>=0 and e['rsi']>=50)
show('OOS_FIRM+VWAP+RSI',lambda e:e['d'] in oos_dates and e['reclaim']>=0.3 and e['rvwap']>=0 and e['rsi']>=50)
show('OOS_CONFIRM3_SUPPORT+ADVANCE',lambda e:e['d'] in oos_dates and e['confirm3'],key='ret3')

print('\n--- OOS SYMBOL DETAIL: BASE VS CONFIRM3 ---')
by=defaultdict(list); by3=defaultdict(list)
for e in events:
 if e['d'] not in oos_dates: continue
 by[e['sym']].append(e)
 if e['confirm3']: by3[e['sym']].append(e)
for s in sorted(by):
 print(s,'BASE',metrics(by[s]),'| CONFIRM3',metrics(by3.get(s,[]),'ret3'))
