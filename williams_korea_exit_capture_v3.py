#!/usr/bin/env python3
import argparse, sqlite3, statistics

SYMS=['005930','114800','043260','058610','122630','257720','233740','080220','950160','466100','439090','484810','950260']

def rsi(vals,p=2):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    g=[]; l=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g)/p; al=sum(l)/p; out[p]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(p+1,len(vals)):
        d=vals[i]-vals[i-1]; ag=(ag*(p-1)+max(d,0))/p; al=(al*(p-1)+max(-d,0))/p
        out[i]=100 if al==0 else 100-100/(1+ag/al)
    return out

def pct(a,b): return (b/a-1)*100 if a else 0

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=100: out[d]=rows
    return out

def entries(prev,cur):
    ph=max(float(x[2]) for x in prev); pl=min(float(x[3]) for x in prev); op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    c=[float(x[4]) for x in cur]; h=[float(x[2]) for x in cur]; lo=[float(x[3]) for x in cur]; r2=rsi(c,2)
    out=[]
    for i in range(2,len(c)-25):
        if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
            j=i+5
            if pct(c[i],c[j])>=0.30: out.append((j,c,h,lo))
    return out

def exit_idx(kind,ei,c,h,l):
    n=len(c); entry=c[ei]
    if kind.startswith('FIX'):
        mins=int(kind[3:]); return min(n-1,ei+mins)
    if kind.startswith('TP'):
        t=float(kind[2:])/100
        for i in range(ei+1,n):
            if h[i]>=entry*(1+t): return i
        return n-1
    if kind.startswith('TR'):
        dd=float(kind[2:])/100; peak=entry
        for i in range(ei+1,n):
            peak=max(peak,h[i])
            if c[i] <= peak*(1-dd): return i
        return n-1
    if kind=='BE03':
        armed=False
        for i in range(ei+1,n):
            if h[i]>=entry*1.003: armed=True
            if armed and c[i]<=entry: return i
        return n-1
    if kind=='BE05':
        armed=False
        for i in range(ei+1,n):
            if h[i]>=entry*1.005: armed=True
            if armed and c[i]<=entry: return i
        return n-1
    return n-1

def metrics(rs):
    if not rs:return None
    gp=sum(x for x in rs if x>0); gl=-sum(x for x in rs if x<0); pf=gp/gl if gl>0 else 999
    eq=peak=0; mdd=0
    for x in rs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return len(rs),statistics.fmean(rs),100*sum(x>0 for x in rs)/len(rs),pf,mdd

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    kinds=['FIX5','FIX10','FIX15','FIX20','TP03','TP05','TP08','TP10','TR02','TR03','TR04','BE03','BE05']
    bucket={k:[] for k in kinds}; total=0
    for s in SYMS:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); n=0
        for di in range(1,len(ds)):
            for ei,c,h,l in entries(dm[ds[di-1]],dm[ds[di]]):
                total+=1; n+=1
                for k in kinds:
                    xi=exit_idx(k,ei,c,h,l); bucket[k].append(pct(c[ei],c[xi]))
        print('AUDIT',s,'DAYS=',max(0,len(ds)-1),'CONFIRM5=',n)
    con.close()
    print('=== WILLIAMS KOREA EXIT CAPTURE V3 ===')
    print('ENTRY=Williams + RSI2>50 + 5m confirmation >= +0.30%')
    print('TOTAL_ENTRIES=',total)
    rank=[]
    for k in kinds:
        m=metrics(bucket[k])
        if not m: continue
        n,avg,win,pf,mdd=m
        print(k,'N=',n,'AVG=',f'{avg:.4f}%','WIN=',f'{win:.2f}%','PF=',f'{pf:.3f}','MDD=',f'{mdd:.4f}%')
        score=avg + 0.05*(pf-1) + 0.002*mdd
        rank.append((score,k,n,avg,win,pf,mdd))
    print('=== QUALITY RANK ===')
    for i,x in enumerate(sorted(rank,reverse=True),1):
        score,k,n,avg,win,pf,mdd=x
        print(i,k,'SCORE=',f'{score:.4f}','N=',n,'AVG=',f'{avg:.4f}%','WIN=',f'{win:.2f}%','PF=',f'{pf:.3f}','MDD=',f'{mdd:.4f}%')

if __name__=='__main__': main()
