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

def vwap_series(b):
 out=[]; pv=0.0; vv=0.0
 for x in b:
  tp=(x['high']+x['low']+x['close'])/3.0; v=max(float(x['volume'] or 0),0.0); pv+=tp*v; vv+=v; out.append(pv/vv if vv>0 else x['close'])
 return out

def metrics(rs):
 if not rs:return 'N=0'
 gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=(gp/gl if gl>0 else float('inf')); wins=sum(x>0 for x in rs)
 return f"N={len(rs)} WIN={wins/len(rs)*100:.1f}% AVG_RET={sum(rs)/len(rs):.3f}% PF={pf:.3f} MED={statistics.median(rs):.3f}%"

events=[]
for (sym,d),b in G.items():
 if len(b)<45: continue
 c=[x['close'] for x in b]; mh=macdh(c); vw=vwap_series(b); swings=[]
 for i in range(5,len(b)-3):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1]); imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  br=None
  for j2 in range(i,min(len(b)-3,i+120)):
   if b[j2]['close']<l2: br=j2; break
  if br is None: continue
  rc=None
  for j2 in range(br+1,min(len(b)-3,br+21)):
   if b[j2]['close']>=l2: rc=j2; break
  if rc is None or rc<1: continue
  macd_ok=mh[rc]>mh[rc-1]
  if not macd_ok: continue
  firm=(b[rc]['close']/l2-1)*100>=0.3
  above=b[rc]['close']>=vw[rc]
  ret=(b[-1]['close']/b[rc]['close']-1)*100
  events.append(dict(sym=sym,d=d,ret=ret,firm=firm,above=above))

all_dates=sorted(set(e['d'] for e in events)); cut=max(1,len(all_dates)//2); oos_dates=set(all_dates[cut:]); oos=[e for e in events if e['d'] in oos_dates]

def show(label,es): print(label,metrics([e['ret'] for e in es]))
print('=== WILLIAMS KOREA REENTRY RECOVERY STATE EX-DOMINANT V86 ===')
print('Freeze V84/V85 recovery states. Diagnose whether VWAP/firm edge survives without explosive symbols; no retuning.')
for name,pred in [('BASE',lambda e:True),('VWAP',lambda e:e['above']),('FIRM',lambda e:e['firm']),('FIRM_VWAP',lambda e:e['firm'] and e['above'])]:
 es=[e for e in oos if pred(e)]
 print(f'\n--- OOS {name} ---'); show('ALL',es)
 for ex in [('950260',),('950260','950160'),('950260','950160','080220','466100')]:
  z=[e for e in es if e['sym'] not in ex]; show('EX_'+('_'.join(ex)),z)
  by=defaultdict(list)
  for e in z: by[e['sym']].append(e['ret'])
  if by:
   avgs=[sum(v)/len(v) for v in by.values()]; wins=[sum(x>0 for x in v)/len(v)*100 for v in by.values()]
   print(f"  SYMBOL_BAL GROUPS={len(by)} AVG_RET={sum(avgs)/len(avgs):.3f}% WIN={sum(wins)/len(wins):.1f}%")

print('\n--- EX950260 SYMBOL DETAIL: BASE vs VWAP vs FIRM_VWAP ---')
for sym in sorted(set(e['sym'] for e in oos if e['sym']!='950260')):
 b=[e['ret'] for e in oos if e['sym']==sym]
 v=[e['ret'] for e in oos if e['sym']==sym and e['above']]
 f=[e['ret'] for e in oos if e['sym']==sym and e['above'] and e['firm']]
 print(sym,'BASE',metrics(b),'| VWAP',metrics(v),'| FIRM_VWAP',metrics(f))
