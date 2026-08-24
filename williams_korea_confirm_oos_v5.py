#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def rsi(vals,p=2):
    n=len(vals); out=[None]*n
    if n<p+2: return out
    gs=[]; ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=20: out[d]=rows
    return out

def raw(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    closes=[float(r[4]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-15):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes))
    return out

def metrics(xs):
    if not xs: return {'n':0,'avg':0,'win':0,'pf':0,'mdd':0}
    avg=statistics.fmean(xs); win=100*sum(x>0 for x in xs)/len(xs)
    gp=sum(x for x in xs if x>0); gl=-sum(x for x in xs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=mdd=0
    for x in xs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(xs),'avg':avg,'win':win,'pf':pf,'mdd':mdd}

def show(name,m):
    print(f"{name} N={m['n']} AVG={m['avg']:.4f}% WIN={m['win']:.2f}% PF={m['pf']:.3f} MDD={m['mdd']:.4f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    rec=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]
            for i,closes in raw(dm[ds[di-1]],dm[d]):
                j=i+4
                if j<len(closes) and pct(closes[i],closes[j])>=0.40:
                    k=min(j+5,len(closes)-1)
                    rec.append((d,pct(closes[j],closes[k])))
    dates=sorted(set(d for d,_ in rec)); split=len(dates)//2
    isd=set(dates[:split]); oosd=set(dates[split:])
    allm=metrics([r for _,r in rec]); ism=metrics([r for d,r in rec if d in isd]); oosm=metrics([r for d,r in rec if d in oosd])
    print('=== WILLIAMS KOREA CONFIRM OOS V5 ===')
    print('RULE=Williams CrossUp + RSI2>50 + 4m confirmation >= +0.40%; exit=5m fixed hold for validation only')
    print('DATE_RANGE=',(dates[0] if dates else None, dates[-1] if dates else None),'UNIQUE_DATES=',len(dates),'IS=',len(isd),'OOS=',len(oosd))
    show('ALL',allm); show('IS ',ism); show('OOS',oosm)
    passed=bool(oosm['n']>=10 and oosm['avg']>0 and oosm['pf']>1.0 and oosm['mdd']>-10)
    print('OOS_PASS=',passed)
    con.close()
if __name__=='__main__': main()
