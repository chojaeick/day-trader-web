#!/usr/bin/env python3
import argparse, sqlite3, statistics

SYMS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
MODES=['MACD_CCI','MACD_2BAR','CCI_2BAR','TRAIL_03','TRAIL_05','MA5_EXIT','HYBRID_TRAIL']

def ema(v,n):
    if not v:return []
    a=2/(n+1); o=[float(v[0])]
    for x in v[1:]: o.append(a*float(x)+(1-a)*o[-1])
    return o

def rsi(v,n=2):
    o=[None]*len(v)
    if len(v)<n+2:return o
    g=[]; l=[]
    for i in range(1,n+1):
        d=v[i]-v[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g)/n; al=sum(l)/n; o[n]=100 if al==0 else 100-100/(1+ag/al)
    for i in range(n+1,len(v)):
        d=v[i]-v[i-1]; ag=(ag*(n-1)+max(d,0))/n; al=(al*(n-1)+max(-d,0))/n
        o[i]=100 if al==0 else 100-100/(1+ag/al)
    return o

def cci(h,l,c,n=20):
    tp=[(a+b+d)/3 for a,b,d in zip(h,l,c)]; o=[None]*len(tp)
    for i in range(n-1,len(tp)):
        w=tp[i-n+1:i+1]; m=sum(w)/n; md=sum(abs(x-m) for x in w)/n
        o[i]=0 if md==0 else (tp[i]-m)/(0.015*md)
    return o

def pct(a,b): return (b/a-1)*100 if a else 0

def load(con,s,maxd):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(s,maxd+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        r=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(s,d)).fetchall()
        if r: out[d]=r
    return out

def inds(rows):
    h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]; c=[float(r[4]) for r in rows]
    r2=rsi(c,2); cc=cci(h,l,c,20); e12=ema(c,12); e26=ema(c,26); m=[a-b for a,b in zip(e12,e26)]; sg=ema(m,9); hist=[a-b for a,b in zip(m,sg)]
    ma5=[]
    for i in range(len(c)):
        w=c[max(0,i-4):i+1]; ma5.append(sum(w)/len(w))
    return h,l,c,r2,cc,m,sg,hist,ma5

def entries(prev,cur):
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); trig=float(cur[0][1])+0.5*(ph-pl)
    h,l,c,r2,cc,m,sg,hist,ma5=inds(cur); out=[]
    for i in range(2,len(cur)-6):
        if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
            j=i+5
            if pct(c[i],c[j])>=0.30:
                out.append({'i':j,'entry':c[j],'h':h,'l':l,'c':c,'cci':cc,'m':m,'sg':sg,'hist':hist,'ma5':ma5})
    return out

def exit_ix(e,mode):
    i0=e['i']; c=e['c']; h=e['h']; cc=e['cci']; m=e['m']; sg=e['sg']; ma5=e['ma5']; peak=e['entry']; weak=0
    for i in range(i0+1,len(c)):
        peak=max(peak,h[i]); dd=pct(peak,c[i])
        cdown=cc[i] is not None and cc[i-1] is not None and cc[i]<cc[i-1]
        mb=m[i]<sg[i]
        if mode=='MACD_CCI' and mb and cdown:return i
        if mode=='MACD_2BAR':
            weak=weak+1 if mb else 0
            if weak>=2:return i
        elif mode=='CCI_2BAR':
            weak=weak+1 if cdown else 0
            if weak>=2:return i
        elif mode=='TRAIL_03' and dd<=-0.30:return i
        elif mode=='TRAIL_05' and dd<=-0.50:return i
        elif mode=='MA5_EXIT' and c[i]<ma5[i]:return i
        elif mode=='HYBRID_TRAIL':
            if mb and cdown:return i
            if pct(e['entry'],peak)>=0.50 and dd<=-0.30:return i
    return len(c)-1

def metrics(ts):
    if not ts:return None
    rs=[x['ret'] for x in ts]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]; gp=sum(wins); gl=-sum(losses); pf=gp/gl if gl>0 else 999
    eq=peak=0; mdd=0
    for x in rs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(rs),'avg':statistics.fmean(rs),'win':100*len(wins)/len(rs),'pf':pf,'mdd':mdd,'hold':statistics.fmean([x['hold'] for x in ts]),'cap':statistics.fmean([x['cap'] for x in ts if x['cap'] is not None])}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); args=ap.parse_args(); con=sqlite3.connect(args.db)
    alltr={m:[] for m in MODES}; total=0
    for s in SYMS:
        dm=load(con,s,args.max_days); ds=sorted(dm); n=0
        for k in range(1,len(ds)):
            for e in entries(dm[ds[k-1]],dm[ds[k]]):
                n+=1; total+=1
                for mode in MODES:
                    ix=exit_ix(e,mode); ret=pct(e['entry'],e['c'][ix]); mfe=max(0,pct(e['entry'],max(e['h'][e['i']:ix+1]))); cap=(min(100,max(0,ret)/mfe*100) if mfe>0 else None)
                    alltr[mode].append({'ret':ret,'hold':ix-e['i'],'cap':cap})
        print('AUDIT',s,'STRONG5_ENTRIES=',n)
    con.close()
    print('\n=== WILLIAMS EXIT V2 STRONG5 COMPARE ==='); print('ENTRY=5m confirmation: +0.30% from raw Williams arrow')
    rank=[]
    for mode in MODES:
        z=metrics(alltr[mode]);
        if not z:continue
        print(mode,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%",'AVG_HOLD=',f"{z['hold']:.1f}m",'MFE_CAPTURE=',f"{z['cap']:.1f}%")
        score=z['avg']*0.4+(z['pf']-1)*0.2+z['win']/100*0.1+z['mdd']*0.02+z['cap']/100*0.05; rank.append((score,mode,z))
    rank.sort(reverse=True,key=lambda x:x[0]); print('\n=== EXIT QUALITY RANK ===')
    for i,(sc,m,z) in enumerate(rank,1): print(i,m,'SCORE=',f'{sc:.4f}','AVG=',f"{z['avg']:.4f}",'PF=',f"{z['pf']:.3f}",'WIN=',f"{z['win']:.2f}",'MDD=',f"{z['mdd']:.4f}",'CAP=',f"{z['cap']:.1f}")

if __name__=='__main__': main()
