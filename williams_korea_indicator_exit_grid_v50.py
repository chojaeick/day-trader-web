#!/usr/bin/env python3
import argparse, sqlite3, re, statistics
KR_RE=re.compile(r'^\d{6}$')

def pct(a,b): return (b/a-1)*100 if a else 0.0

def ema(vals,p):
    if not vals:return []
    a=2/(p+1); out=[vals[0]]
    for v in vals[1:]: out.append(a*v+(1-a)*out[-1])
    return out

def rsi(vals,p):
    out=[None]*len(vals)
    if len(vals)<p+2:return out
    gs=[];ls=[]
    for i in range(1,p+1):
        d=vals[i]-vals[i-1]; gs.append(max(d,0)); ls.append(max(-d,0))
    ag=sum(gs)/p; al=sum(ls)/p
    out[p]=100 if al==0 else 100-(100/(1+ag/al))
    for i in range(p+1,len(vals)):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(p-1)+g)/p; al=(al*(p-1)+l)/p
        out[i]=100 if al==0 else 100-(100/(1+ag/al))
    return out

def cci(h,l,c,p=9):
    tp=[(h[i]+l[i]+c[i])/3 for i in range(len(c))]
    out=[None]*len(c)
    for i in range(p-1,len(c)):
        w=tp[i-p+1:i+1]; ma=sum(w)/p; md=sum(abs(x-ma) for x in w)/p
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out

def macd(c):
    e12=ema(c,12); e26=ema(c,26)
    m=[e12[i]-e26[i] for i in range(len(c))]
    s=ema(m,9); h=[m[i]-s[i] for i in range(len(c))]
    return m,s,h

def load_days(con,s,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
        if len(rows)>=50: out[d]=rows
    return out

def entry_index(prev,cur):
    c=[float(r[4]) for r in cur]
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); op=float(cur[0][1])
    trig=op+0.5*(ph-pl); r2=rsi(c,2)
    for i in range(3,len(cur)-35):
        if c[i-1] <= trig < c[i] and r2[i] is not None and r2[i]>50:
            return i+1
    return None

def run(rows,ei,rule):
    c=[float(r[4]) for r in rows]; h=[float(r[2]) for r in rows]; l=[float(r[3]) for r in rows]
    r14=rsi(c,14); cc=cci(h,l,c,9); ml,ms,mh=macd(c)
    entry=c[ei]; peak=entry; exit_i=len(rows)-1; reason='EOD'; ever_strong=False
    for i in range(max(ei+1,15),len(rows)):
        peak=max(peak,h[i]); runup=pct(entry,peak); ret=pct(entry,c[i])
        rs=r14[i] if r14[i] is not None else 50; rs1=(r14[i]-r14[i-1]) if r14[i-1] is not None else 0
        cs=cc[i] if cc[i] is not None else 0; cs1=(cc[i]-cc[i-1]) if cc[i-1] is not None else 0
        msl=ml[i]-ml[i-1]; ssl=ms[i]-ms[i-1]; hsl=mh[i]-mh[i-1]
        strong_parts=[rs>=70 and rs1>0, cs>=100 and cs1>0, ml[i]>ms[i] and msl>0 and ssl>=0]
        strong=sum(strong_parts)
        if strong>=2 or runup>=3: ever_strong=True

        rsi_exit=(rs<75 and rs1<0)
        cci_exit=(cs<100 and cs1<0)
        macd_exit=(ml[i]<ms[i] and msl<0) or (msl<0 and ssl<0 and hsl<0)
        negs=sum([rsi_exit,cci_exit,macd_exit])

        if ever_strong:
            if rule=='OR1' and negs>=1:
                exit_i=i; reason='STRONG_OR1'; break
            if rule=='AND2' and negs>=2:
                exit_i=i; reason='STRONG_AND2'; break
            if rule=='AND3' and negs>=3:
                exit_i=i; reason='STRONG_AND3'; break
            if rule=='RSI_CCI' and rsi_exit and cci_exit:
                exit_i=i; reason='RSI_CCI'; break
            if rule=='MACD_PLUS' and macd_exit and (rsi_exit or cci_exit):
                exit_i=i; reason='MACD_PLUS'; break
        else:
            # weak signal: fast failure only when at least two momentum axes are negative
            weak_rsi=(rs1<0 and rs<60)
            weak_cci=(cs1<0 and cs<50)
            weak_macd=(msl<0 and ssl<0)
            if i-ei>=2 and sum([weak_rsi,weak_cci,weak_macd])>=2 and ret<=0:
                exit_i=i; reason='WEAK_2OF3'; break

    mfe=max(pct(entry,x) for x in h[ei:]); mae=min(pct(entry,x) for x in l[ei:]); ret=pct(entry,c[exit_i]); cap=ret/mfe*100 if mfe>0 else 0
    return ret,mfe,mae,exit_i-ei,reason,cap

def metrics(name,tr):
    if not tr:
        print(name,'N=0'); return
    vals=[x[2] for x in tr]; gp=sum(x for x in vals if x>0); gl=-sum(x for x in vals if x<0); pf=gp/gl if gl else 999
    eq=pk=mdd=0
    for x in vals:
        eq+=x; pk=max(pk,eq); mdd=min(mdd,eq-pk)
    print(f"{name} N={len(tr)} AVG={statistics.fmean(vals):.3f}% WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% PF={pf:.3f} MDD={mdd:.3f}% HOLD={statistics.fmean(x[5] for x in tr):.1f}m")
    big=[x for x in tr if x[3]>=5]
    if big:
        print(f"  BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))]
    entries=[]
    for s in syms:
        dm=load_days(con,s,args.max_days); ds=sorted(dm)
        for di in range(1,len(ds)):
            d=ds[di]; prev=dm[ds[di-1]]; cur=dm[d]; ei=entry_index(prev,cur)
            if ei is not None: entries.append((d,s,cur,ei))
    dates=sorted(set(x[0] for x in entries)); cut=len(dates)//2; isd=set(dates[:cut]); oos=set(dates[cut:])
    print('=== WILLIAMS KOREA INDICATOR EXIT GRID V50 ===')
    print('ENTRY fixed. Strong/weak interpretation uses only causal RSI14/CCI9/MACD slopes. Test OR/AND exits.')
    print('IS_DATES',','.join(sorted(isd)))
    print('OOS_DATES',','.join(sorted(oos)))
    for rule in ('OR1','AND2','AND3','RSI_CCI','MACD_PLUS'):
        tr=[]
        for d,s,rows,ei in entries:
            ret,mfe,mae,hold,reason,cap=run(rows,ei,rule)
            tr.append((d,s,ret,mfe,mae,hold,reason,cap))
        print('\n---',rule,'---')
        metrics(rule+'_ALL',tr)
        metrics(rule+'_IS',[x for x in tr if x[0] in isd])
        metrics(rule+'_OOS',[x for x in tr if x[0] in oos])
    con.close()

if __name__=='__main__': main()
