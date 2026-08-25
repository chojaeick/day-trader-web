#!/usr/bin/env python3
import argparse, sqlite3
from collections import defaultdict

P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' and session='REGULAR' order by trade_date,symbol,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:G[(r['symbol'],r['trade_date'])].append(r)

def ema(v,n):
 out=[]; k=2/(n+1); x=None
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

def classify_dates(ds):
 ds=sorted(ds); k=max(1,len(ds)//2); return set(ds[:k]),set(ds[k:])
IS,OOS=classify_dates(dates)

events=[]
for (sym,d),b in G.items():
 if len(b)<50: continue
 c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
 swings=[]
 for i in range(5,len(b)-15):
  j=i-2
  if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
  if len(swings)<2 or rr[i] is None: continue
  s2,s1=swings[-1],swings[-2]
  if s2<=s1 or i<s2 or i-s2>10: continue
  l1,l2=b[s1]['low'],b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
  imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
  if not (imp>=2 and 2<hl<=5): continue
  support=l1
  # first close break below prior swing-low, then first reclaim within 10 bars
  br=None
  for k in range(i,min(len(b),i+80)):
   if b[k]['close']<support: br=k; break
  if br is None: continue
  rec=None
  for k in range(br+1,min(len(b),br+11)):
   if b[k]['close']>=support: rec=k; break
  # runner outcome from break bar horizon 120
  end=min(len(b),br+121); p=b[br]['close']; mfe=(max(x['high'] for x in b[br:end])/p-1)*100
  runner=mfe>=3
  rsi_turn=False; macd_up=False
  if rec is not None and rec>0:
   rsi_turn=rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1]
   macd_up=hist[rec]>hist[rec-1]
  events.append(dict(sym=sym,d=d,runner=runner,rec=(None if rec is None else rec-br),rsi=rsi_turn,macd=macd_up,mfe=mfe))

def pred(e,mode):
 if mode=='NO_VETO': return False
 if mode=='RECLAIM2': return e['rec'] is not None and e['rec']<=2
 if mode=='RECLAIM2_MACD': return e['rec'] is not None and e['rec']<=2 and e['macd']
 if mode=='RECLAIM2_RSI_MACD': return e['rec'] is not None and e['rec']<=2 and e['rsi'] and e['macd']
 return False

def report(es,label):
 print('\n---',label,'N=',len(es),'RUNNER=',sum(e['runner'] for e in es),'---')
 for mode in ['NO_VETO','RECLAIM2','RECLAIM2_MACD','RECLAIM2_RSI_MACD']:
  veto=[e for e in es if pred(e,mode)]
  nv=[e for e in es if not pred(e,mode)]
  # Veto usefulness: how many future runners are protected vs how many nonrunners are unnecessarily held
  rp=sum(e['runner'] for e in veto); np=sum(not e['runner'] for e in veto)
  totalr=sum(e['runner'] for e in es); totaln=sum(not e['runner'] for e in es)
  protect=100*rp/totalr if totalr else 0; falsehold=100*np/totaln if totaln else 0
  nv_run=100*sum(e['runner'] for e in nv)/len(nv) if nv else 0
  print(f"{mode} VETO_N={len(veto)} RUNNER_PROTECTED={rp}/{totalr} ({protect:.1f}%) NONRUNNER_HELD={np}/{totaln} ({falsehold:.1f}%) NO_VETO_PATH_RUNNER_RATE={nv_run:.1f}%")

print('=== WILLIAMS KOREA RECLAIM HOLD-VETO OOS V74 ===')
print('Role test only: on structural support break, does fast reclaim justify vetoing an immediate EXIT?')
report(events,'ALL')
report([e for e in events if e['d'] in IS],'IS')
report([e for e in events if e['d'] in OOS],'OOS')
print('\n--- OOS LEAVE-ONE-SYMBOL-OUT: RECLAIM2_MACD ---')
oos=[e for e in events if e['d'] in OOS]
for s in sorted(set(e['sym'] for e in oos)):
 z=[e for e in oos if e['sym']!=s]; v=[e for e in z if pred(e,'RECLAIM2_MACD')]
 if not z or not v: continue
 rr=sum(e['runner'] for e in v)/len(v)*100
 print(f"EX_{s} N={len(v)} VETO_RUNNER_RATE={rr:.1f}%")
