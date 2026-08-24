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
    closes=[float(r[4]) for r in cur]; highs=[float(r[2]) for r in cur]; r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-65):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i]>50:
            out.append((i,closes,highs))
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
    rec={5:[],10:[],20:[],60:[]}
    mfe={20:[],60:[]}
    dates=set()
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]
            for i,closes,highs in raw(dm[ds[di-1]],dm[d]):
                dates.add(d)
                for h in (5,10,20,60):
                    j=min(i+h,len(closes)-1)
                    rec[h].append((d,pct(closes[i],closes[j])))
                for h in (20,60):
                    j=min(i+h,len(closes)-1)
                    peak=max(highs[i+1:j+1]) if j>i else highs[i]
                    mfe[h].append((d,pct(closes[i],peak)))
    dts=sorted(dates); split=len(dts)//2; isd=set(dts[:split]); oosd=set(dts[split:])
    print('=== WILLIAMS KOREA RAW OOS V7 ===')
    print('RULE=Raw Williams CrossUp + RSI2>50, no confirmation filter')
    print('DATES=',dts,'IS=',len(isd),'OOS=',len(oosd))
    for h in (5,10,20,60):
        print(f'--- CLOSE H{h} ---')
        show('ALL',metrics([x for _,x in rec[h]]))
        show('IS ',metrics([x for d,x in rec[h] if d in isd]))
        show('OOS',metrics([x for d,x in rec[h] if d in oosd]))
    for h in (20,60):
        vals=[x for _,x in mfe[h]]
        oos=[x for d,x in mfe[h] if d in oosd]
        def rate(arr,t): return 100*sum(x>=t for x in arr)/len(arr) if arr else 0
        print(f'MFE H{h} ALL_N={len(vals)} AVG={statistics.fmean(vals):.4f}% >=0.30={rate(vals,0.30):.2f}% >=0.50={rate(vals,0.50):.2f}% >=1.00={rate(vals,1.00):.2f}%')
        print(f'MFE H{h} OOS_N={len(oos)} AVG={statistics.fmean(oos):.4f}% >=0.30={rate(oos,0.30):.2f}% >=0.50={rate(oos,0.50):.2f}% >=1.00={rate(oos,1.00):.2f}%')
    con.close()
if __name__=='__main__': main()
