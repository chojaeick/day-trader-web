#!/usr/bin/env python3
import argparse, sqlite3, re
from collections import defaultdict

p=argparse.ArgumentParser(); p.add_argument('--max-days',type=int,default=20); p.add_argument('--db',default='daytrader.db'); a=p.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close,volume from historical_minute_bars where trade_date in ({qs}) and session='REGULAR' order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:
    s=str(r['symbol'])
    if re.fullmatch(r'\d{6}',s): G[(s,r['trade_date'])].append(r)

def ema(x,n):
    if not x:return []
    k=2/(n+1); o=[x[0]]
    for v in x[1:]:o.append(v*k+o[-1]*(1-k))
    return o

def rsi(c,n=14):
    o=[None]*len(c); g=[]; l=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; g.append(max(d,0)); l.append(max(-d,0))
        if i>=n:
            ag=sum(g[i-n:i])/n; al=sum(l[i-n:i])/n; o[i]=100 if al==0 else 100-100/(1+ag/al)
    return o

def mfe(b,i,h=120):
    p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
    return (max(x['high'] for x in z)/p-1)*100

events=[]
for (sym,d),b in G.items():
    if len(b)<50: continue
    c=[x['close'] for x in b]; rr=rsi(c); e12=ema(c,12); e26=ema(c,26); mac=[x-y for x,y in zip(e12,e26)]; sig=ema(mac,9); hist=[x-y for x,y in zip(mac,sig)]
    swings=[]
    for i in range(5,len(b)):
        j=i-2
        if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
        if len(swings)<2: continue
        s2=swings[-1]; s1=swings[-2]
        if s2<=s1 or i<s2 or i-s2>10: continue
        l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
        imp=(hi/l1-1)*100; hl=(l2/l1-1)*100
        if not (imp>=2 and 2<hl<=5): continue
        support=l2
        # Find first close below support after current structural event, then first reclaim <=10 bars.
        br=None
        for k in range(i,min(len(b),i+61)):
            if b[k]['close']<support:
                br=k; break
        if br is None: continue
        rec=None
        for k in range(br+1,min(len(b),br+11)):
            if b[k]['close']>=support:
                rec=k; break
        m=mfe(b,i); runner=m>=3
        if rec is None:
            events.append(dict(sym=sym,d=d,runner=runner,kind='NO_RECLAIM10'))
            continue
        lag=rec-br
        rturn=rr[rec] is not None and rr[rec-1] is not None and rr[rec]>rr[rec-1]
        mup=hist[rec]>hist[rec-1]
        events.append(dict(sym=sym,d=d,runner=runner,kind='RECLAIM',lag=lag,rsi=rturn,macd=mup))

def stat(name,fn):
    z=[e for e in events if fn(e)]
    if not z: print(name,'N=0'); return
    r=sum(e['runner'] for e in z)
    print(f"{name} N={len(z)} RUNNER={r}/{len(z)} ({r/len(z)*100:.1f}%)")

print('=== WILLIAMS KOREA RECLAIM ROLE DECISION V73 ===')
print('Decision audit: is reclaim quality robust enough to be an EXIT veto/HOLD context, not an entry filter?')
stat('NO_RECLAIM<=10',lambda e:e['kind']=='NO_RECLAIM10')
stat('RECLAIM<=2',lambda e:e['kind']=='RECLAIM' and e['lag']<=2)
stat('RECLAIM<=2+RSI',lambda e:e['kind']=='RECLAIM' and e['lag']<=2 and e['rsi'])
stat('RECLAIM<=2+MACD',lambda e:e['kind']=='RECLAIM' and e['lag']<=2 and e['macd'])
stat('RECLAIM<=2+RSI+MACD',lambda e:e['kind']=='RECLAIM' and e['lag']<=2 and e['rsi'] and e['macd'])
print('\n--- LEAVE ONE OUT: RECLAIM<=2+RSI+MACD ---')
syms=sorted({e['sym'] for e in events})
for s in syms:
    z=[e for e in events if e['sym']!=s and e['kind']=='RECLAIM' and e['lag']<=2 and e['rsi'] and e['macd']]
    if not z: continue
    r=sum(e['runner'] for e in z)
    print(f"EX_{s} N={len(z)} RUNNER={r/len(z)*100:.1f}%")
print('\n--- ROLE DECISION ---')
print('If reclaim+RSI+MACD materially beats no-reclaim but collapses when one symbol is removed, use only as secondary HOLD/EXIT-veto evidence.')
