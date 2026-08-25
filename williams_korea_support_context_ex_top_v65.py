#!/usr/bin/env python3
import argparse, sqlite3, re
from collections import defaultdict
KR_RE=re.compile(r'^\d{6}$')
P=argparse.ArgumentParser(); P.add_argument('--max-days',type=int,default=20); P.add_argument('--db',default='daytrader.db'); a=P.parse_args()
con=sqlite3.connect(a.db); con.row_factory=sqlite3.Row
dates=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where interval_min=1 order by trade_date desc limit ?",(a.max_days,))]
if not dates: raise SystemExit('NO DATA')
qs=','.join('?'*len(dates))
rows=con.execute(f"select symbol,trade_date,et_time,open,high,low,close from historical_minute_bars where interval_min=1 and trade_date in ({qs}) order by symbol,trade_date,et_time",dates).fetchall()
G=defaultdict(list)
for r in rows:
    if KR_RE.match(str(r['symbol'] or '')): G[(r['symbol'],r['trade_date'])].append(r)

def rsi(c,n=14):
    out=[None]*len(c); gains=[]; losses=[]
    for i in range(1,len(c)):
        d=c[i]-c[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
        if i>=n:
            ag=sum(gains[i-n:i])/n; al=sum(losses[i-n:i])/n
            out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def mfe_mae(b,i,h=120):
    p=b[i]['close']; z=b[i:min(len(b),i+h+1)]
    return (max(x['high'] for x in z)/p-1)*100,(min(x['low'] for x in z)/p-1)*100

events=[]
for (sym,d),b in G.items():
    if len(b)<40: continue
    c=[x['close'] for x in b]; rr=rsi(c)
    swings=[]; seen=set()
    for i in range(5,len(b)):
        j=i-2
        if b[j]['low']<=min(b[k]['low'] for k in range(j-2,j+3)): swings.append(j)
        if len(swings)<2 or rr[i] is None or rr[i-1] is None: continue
        s2,s1=swings[-1],swings[-2]
        if s2<=s1 or i-s2>10 or i<s2: continue
        key=(sym,d,s1,s2,i)
        if key in seen: continue
        l1=b[s1]['low']; l2=b[s2]['low']; hi=max(x['high'] for x in b[s1:s2+1])
        imp=(hi/l1-1)*100; hl=(l2/l1-1)*100; reclaim=(b[i]['close']/l2-1)*100; age=i-s2
        bullish=b[i]['close']>b[i]['open']; rturn=rr[i]>rr[i-1]
        if not (imp>=1 and hl>=-0.8 and bullish and rturn and reclaim>=0.2): continue
        seen.add(key); mfe,mae=mfe_mae(b,i)
        events.append(dict(sym=sym,d=d,imp=imp,hl=hl,age=age,mfe=mfe,mae=mae))

def subset(es,name):
    if name=='BASE': return es
    if name=='HL_STRONG+IMP2': return [e for e in es if 2<e['hl']<=5 and e['imp']>=2]
    if name=='IMP5+FAST': return [e for e in es if e['imp']>=5 and e['age']<=2]
    return []

def sm(es):
    if not es:return 'N=0'
    n=len(es); return f"N={n} MFE_AVG={sum(e['mfe'] for e in es)/n:.2f}% MAE_AVG={sum(e['mae'] for e in es)/n:.2f}% MFE>=1={100*sum(e['mfe']>=1 for e in es)/n:.1f}% MFE>=3={100*sum(e['mfe']>=3 for e in es)/n:.1f}% MFE>=5={100*sum(e['mfe']>=5 for e in es)/n:.1f}%"

def symbal(es):
    g=defaultdict(list)
    for e in es:g[e['sym']].append(e)
    if not g:return 'GROUPS=0'
    vals=[]
    for s,z in g.items():
        vals.append((sum(e['mfe']>=3 for e in z)/len(z)*100,sum(e['mfe']>=5 for e in z)/len(z)*100,sum(e['mfe'] for e in z)/len(z)))
    return f"GROUPS={len(vals)} MFE_AVG={sum(x[2] for x in vals)/len(vals):.2f}% MFE>=3={sum(x[0] for x in vals)/len(vals):.1f}% MFE>=5={sum(x[1] for x in vals)/len(vals):.1f}%"

print('=== WILLIAMS KOREA SUPPORT CONTEXT EX-TOP V65 ===')
print('Robustness test: remove dominant explosive symbols instead of retuning thresholds.')
for name in ['BASE','HL_STRONG+IMP2','IMP5+FAST']:
    base=subset(events,name)
    print('\n---',name,'---')
    for label,exclude in [('ALL',set()),('EX950260',{'950260'}),('EX950260_950160',{'950260','950160'}),('EX_TOP4',{'950260','950160','080220','233740'})]:
        z=[e for e in base if e['sym'] not in exclude]
        print(label,sm(z),'| SYMBOL_BAL',symbal(z))
con.close()
