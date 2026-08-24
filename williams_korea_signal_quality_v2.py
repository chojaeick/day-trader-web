#!/usr/bin/env python3
import argparse, sqlite3, re, statistics

KR_RE=re.compile(r'^\d{6}$')

def rsi(vals, period=2):
    n=len(vals); out=[None]*n
    if n<period+2:return out
    gains=[]; losses=[]
    for i in range(1,period+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(period+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out

def pct(a,b): return (b/a-1)*100 if a else 0.0

def load_days(con,symbol,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(symbol,d)).fetchall()
        if len(rows)>=30: out[d]=rows
    return out

def signals(prev,cur,symbol,date):
    if len(prev)<30 or len(cur)<70:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]
    r2=rsi(closes,2); out=[]
    for i in range(2,len(cur)-61):
        if not (closes[i-1] <= trig < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        entry=closes[i]
        h5=max(highs[i:i+6]); h10=max(highs[i:i+11]); h20=max(highs[i:i+21]); h60=max(highs[i:i+61])
        l20=min(lows[i:i+21]); l60=min(lows[i:i+61])
        confirm5=(i+5<len(closes) and pct(entry,closes[i+5])>=0.30)
        out.append({'symbol':symbol,'date':date,'time':cur[i][0],'entry':entry,
                    'mfe5':pct(entry,h5),'mfe10':pct(entry,h10),'mfe20':pct(entry,h20),'mfe60':pct(entry,h60),
                    'mae20':pct(entry,l20),'mae60':pct(entry,l60),'confirm5':confirm5,
                    'post20_mfe':pct(closes[i+5],max(highs[i+5:i+26])) if confirm5 and i+25<len(highs) else None,
                    'post20_close':pct(closes[i+5],closes[i+25]) if confirm5 and i+25<len(closes) else None})
    return out

def rate(rows,key,thr):
    return 100*sum(1 for x in rows if x[key]>=thr)/len(rows) if rows else 0

def avg(rows,key):
    v=[x[key] for x in rows if x.get(key) is not None]
    return statistics.fmean(v) if v else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=20); ap.add_argument('--max-symbols',type=int,default=13); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    kr=con.execute("select symbol,count(distinct trade_date) days,count(*) rows from historical_minute_bars where interval_min=1 and symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' group by symbol order by days desc,rows desc limit ?",(args.max_symbols,)).fetchall()
    print('=== WILLIAMS KOREA SIGNAL QUALITY V2 ===')
    print('RULE=Williams CrossUp + RSI2>50. No exit rule. Measure raw expansion first.')
    allrows=[]
    for s,days,_ in kr:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); n0=len(allrows)
        for di in range(1,len(ds)):
            allrows.extend(signals(dm[ds[di-1]],dm[ds[di]],s,ds[di]))
        print('AUDIT',s,'DAYS=',max(0,len(ds)-1),'RAW_SIGNALS=',len(allrows)-n0)
    con.close()
    print('TOTAL_RAW_SIGNALS=',len(allrows))
    if not allrows:return
    for h in (5,10,20,60):
        k=f'mfe{h}'
        print(f'H{h}', 'MFE_AVG=',f'{avg(allrows,k):.4f}%', 'MFE>0=',f'{rate(allrows,k,0.0000001):.2f}%', '>=0.30=',f'{rate(allrows,k,0.30):.2f}%', '>=0.50=',f'{rate(allrows,k,0.50):.2f}%', '>=1.00=',f'{rate(allrows,k,1.00):.2f}%')
    conf=[x for x in allrows if x['confirm5']]
    print('CONFIRM5_N=',len(conf),'CONFIRM5_RATE=',f'{100*len(conf)/len(allrows):.2f}%')
    if conf:
        print('CONFIRM5_POST20_MFE_AVG=',f'{avg(conf,"post20_mfe"):.4f}%','POST20_CLOSE_AVG=',f'{avg(conf,"post20_close"):.4f}%')
        print('CONFIRM5_POST20_MFE>=0.30=',f'{rate([x for x in conf if x.get("post20_mfe") is not None],"post20_mfe",0.30):.2f}%')
        print('CONFIRM5_POST20_MFE>=0.50=',f'{rate([x for x in conf if x.get("post20_mfe") is not None],"post20_mfe",0.50):.2f}%')
    print('=== BY SYMBOL ===')
    for s,_,_ in kr:
        z=[x for x in allrows if x['symbol']==s]
        if not z: continue
        c=[x for x in z if x['confirm5']]
        print(s,'N=',len(z),'MFE20>=0.50=',f'{rate(z,"mfe20",0.50):.2f}%','MFE60>=1.00=',f'{rate(z,"mfe60",1.00):.2f}%','CONF5=',len(c),f'({100*len(c)/len(z):.1f}%)')

if __name__=='__main__': main()
