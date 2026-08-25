#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

p=argparse.ArgumentParser(); p.add_argument('--max-days',type=int,default=20); p.add_argument('--db',default='daytrader.db'); a=p.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' and length(symbol)=6 order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def local_swing_lows(b):
 out=[]
 for i in range(4,len(b)-2):
  if b[i]['low']<=min(b[j]['low'] for j in range(i-2,i+3)): out.append(i)
 return out

def ctxs(b):
 sw=local_swing_lows(b); out=[]
 for k in range(1,len(sw)):
  s1,s2=sw[k-1],sw[k]
  if s2<=s1: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if imp>=2 and 2<hl<=5: out.append((s2,l2,imp,hl))
 return out

def first_break(b,start,support):
 for i in range(start+1,len(b)):
  if b[i]['close']<support:return i
 return None

def first_reentry(b,br,support,look=20):
 # structural recovery: close back over support and higher close vs prior bar
 end=min(len(b),br+look+1)
 for i in range(br+1,end):
  if b[i]['close']>support and b[i]['close']>b[i-1]['close']:
   return i
 return None

def ret(a,b): return (b/a-1)*100

def eval_set(es):
 if not es:return 'N=0'
 n=len(es); wins=sum(x['ret']>0 for x in es); grossp=sum(max(x['ret'],0) for x in es); grossl=-sum(min(x['ret'],0) for x in es)
 pf=grossp/grossl if grossl>0 else 999.0
 return f"N={n} WIN={wins/n*100:.1f}% AVG_RET={sum(x['ret'] for x in es)/n:.3f}% PF={pf:.3f}"

tr=[]
for (sym,d),b in G.items():
 if len(b)<40:continue
 for s2,sup,imp,hl in ctxs(b):
  br=first_break(b,s2,sup)
  if br is None: continue
  px=b[br]['close']
  # immediate exit baseline ends here
  tr.append(dict(sym=sym,d=d,kind='IMMEDIATE_EXIT',ret=ret(b[s2]['close'],px)))
  re=first_reentry(b,br,sup,20)
  if re is None:
   tr.append(dict(sym=sym,d=d,kind='EXIT_NO_REENTRY',ret=ret(b[s2]['close'],px)))
   continue
  # exit at break, re-enter on recovery; hold re-entry to EOD for diagnostic
  leg1=ret(b[s2]['close'],px); leg2=ret(b[re]['close'],b[-1]['close'])
  tr.append(dict(sym=sym,d=d,kind='EXIT_REENTER20',ret=leg1+leg2))
  # also 5/10-bar forward from reentry, capped by EOD
  for h in (5,10,20,60):
   j=min(len(b)-1,re+h)
   tr.append(dict(sym=sym,d=d,kind=f'REENTER_FWD{h}',ret=leg1+ret(b[re]['close'],b[j]['close'])))

print('=== WILLIAMS KOREA STRUCTURE EXIT + REENTRY AUDIT V78 ===')
print('Question: after a real support break, is it better to exit immediately and re-enter only after structural recovery, instead of waiting through the break?')
for kind in ['IMMEDIATE_EXIT','EXIT_NO_REENTRY','EXIT_REENTER20','REENTER_FWD5','REENTER_FWD10','REENTER_FWD20','REENTER_FWD60']:
 es=[x for x in tr if x['kind']==kind]
 print(kind,eval_set(es))
# date split
all_dates=sorted(set(x['d'] for x in tr))
cut=all_dates[len(all_dates)//2] if all_dates else ''
print('--- IS/OOS EXIT_REENTER20 ---')
for label,fn in [('IS',lambda d:d<cut),('OOS',lambda d:d>=cut)]:
 es=[x for x in tr if x['kind']=='EXIT_REENTER20' and fn(x['d'])]
 print(label,eval_set(es))
