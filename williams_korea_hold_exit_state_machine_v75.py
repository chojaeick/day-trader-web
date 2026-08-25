#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:
 if str(r['symbol']).isdigit() and len(str(r['symbol']))==6: G[(r['symbol'],r['trade_date'])].append(r)

def ema(v,n):
 out=[]; e=None; k=2/(n+1)
 for x in v:
  e=x if e is None else x*k+e*(1-k); out.append(e)
 return out

def rsi(v,n=14):
 out=[None]*len(v); g=[]; l=[]
 for i in range(1,len(v)):
  d=v[i]-v[i-1]; g.append(max(d,0)); l.append(max(-d,0))
  if i>=n:
   ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; out[i]=100 if al==0 else 100-100/(1+ag/al)
 return out

def macdh(v):
 e12=ema(v,12); e26=ema(v,26); m=[x-y for x,y in zip(e12,e26)]; s=ema(m,9); return [x-y for x,y in zip(m,s)]

def first_hit(b,start,thr,side):
 for j in range(start+1,len(b)):
  if side=='up' and b[j]['high']>=thr:return j
  if side=='dn' and b[j]['close']<=thr:return j
 return None

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); mh=macdh(c)
 swings=[]
 for i in range(5,len(b)-1):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None: continue
  s2,s1=swings[-1],swings[-2]
  if i-s2>10 or s2<=s1: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  entry=b[i]['close']; target=entry*1.03
  t3=first_hit(b,i,target,'up')
  # first support break after context
  br=None
  for j in range(i+1,len(b)):
   if b[j]['close']<l2:
    br=j; break
  if br is None:
   exit_i=min(len(b)-1,i+120); reason='NO_BREAK_TIMEOUT'
  else:
   # wait max 2 bars for reclaim. veto only if reclaim and MACD hist improving at reclaim.
   rec=None
   for j in range(br+1,min(len(b),br+3)):
    if b[j]['close']>=l2:
     rec=j; break
   veto=False
   if rec is not None and rec>0 and mh[rec] is not None and mh[rec-1] is not None and mh[rec]>mh[rec-1]: veto=True
   if not veto:
    exit_i=br; reason='STRUCT_BREAK_EXIT'
   else:
    # after veto, continue until next break that fails 2-bar reclaim; otherwise cap 120 bars
    exit_i=min(len(b)-1,i+120); reason='VETO_HOLD_TIMEOUT'
    k=rec+1
    while k<len(b) and k<=i+120:
     if b[k]['close']<l2:
      rec2=None
      for q in range(k+1,min(len(b),k+3)):
       if b[q]['close']>=l2: rec2=q; break
      veto2=False
      if rec2 is not None and rec2>0 and mh[rec2] is not None and mh[rec2-1] is not None and mh[rec2]>mh[rec2-1]: veto2=True
      if not veto2:
       exit_i=k; reason='REBREAK_EXIT'; break
      k=rec2+1; continue
     k+=1
  ret=(b[exit_i]['close']/entry-1)*100
  events.append((d,sym,i,t3,ret,reason))

print('=== WILLIAMS KOREA STRUCTURE-FIRST HOLD/EXIT STATE MACHINE V75 ===')
print('Context: HL_STRONG+IMP2. Rule: support break => wait up to 2 bars; reclaim + MACD hist improvement vetoes exit; otherwise exit. Diagnostic only.')
for tag,es in [('ALL',events)]:
 n=len(es); wins=sum(x[4]>0 for x in es); avg=sum(x[4] for x in es)/n if n else 0
 run=[x for x in es if x[3] is not None]; protected=sum(1 for x in run if x[5] in ('VETO_HOLD_TIMEOUT','REBREAK_EXIT'))
 print(f'{tag} N={n} WIN={wins/n*100 if n else 0:.1f}% AVG_RET={avg:.3f}% RUNNERS={len(run)} VETO_PATH_RUNNERS={protected}/{len(run) if run else 0} ({protected/len(run)*100 if run else 0:.1f}%)')
 rc=defaultdict(list)
 for x in es: rc[x[5]].append(x[4])
 for k,v in sorted(rc.items()): print(f'  {k} N={len(v)} AVG_RET={sum(v)/len(v):.3f}% WIN={sum(x>0 for x in v)/len(v)*100:.1f}%')
# chronological IS/OOS split by date
sd=sorted(set(x[0] for x in events)); cut=max(1,len(sd)//2); isd=set(sd[:cut]); oosd=set(sd[cut:])
for tag,S in [('IS',isd),('OOS',oosd)]:
 es=[x for x in events if x[0] in S]; n=len(es)
 if not n: continue
 print(f'{tag} N={n} WIN={sum(x[4]>0 for x in es)/n*100:.1f}% AVG_RET={sum(x[4] for x in es)/n:.3f}%')
