#!/usr/bin/env python3
import argparse, sqlite3, statistics

DEFAULT_SYMBOLS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']


def ema(vals, span):
    if not vals:return []
    a=2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


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

def mean(xs): return statistics.fmean(xs) if xs else float('nan')


def load_days(con,symbol,max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 and session='REGULAR' order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 and session='REGULAR' order by et_time",(symbol,d)).fetchall()
        if rows: out[d]=rows
    return out


def build_arrays(rows):
    highs=[float(r[2]) for r in rows]; lows=[float(r[3]) for r in rows]; closes=[float(r[4]) for r in rows]; vols=[float(r[5] or 0) for r in rows]
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9); hist=[a-b for a,b in zip(macd,sig)]
    r2=rsi(closes,2)
    vwap=[]; pv=0.0; vv=0.0
    for h,l,c,v in zip(highs,lows,closes,vols):
        tp=(h+l+c)/3.0; pv+=tp*v; vv+=v; vwap.append(pv/vv if vv>0 else c)
    return highs,lows,closes,vols,r2,macd,sig,hist,vwap


def raw_signals(prev,cur):
    if len(prev)<100 or len(cur)<65:return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev); day_open=float(cur[0][1]); trigger=day_open+0.5*(ph-pl)
    highs,lows,closes,vols,r2,macd,sig,hist,vwap=build_arrays(cur)
    out=[]
    for i in range(2,len(cur)-61):
        if not (closes[i-1] <= trigger < closes[i]): continue
        if r2[i] is None or r2[i] <= 50: continue
        out.append({'i':i,'time':cur[i][0],'entry':closes[i],'trigger':trigger,'highs':highs,'lows':lows,'closes':closes,'vols':vols,'hist':hist,'macd':macd,'sig':sig,'vwap':vwap})
    return out


def consecutive_hh_hl(highs,lows,start,end):
    hh=0; hl=0
    for k in range(start+1,end+1):
        if highs[k] > highs[k-1]: hh+=1
        if lows[k] > lows[k-1]: hl+=1
    return hh,hl


def enrich(s):
    i=s['i']; e=s['entry']; H=s['highs']; L=s['lows']; C=s['closes']; V=s['vols']; hist=s['hist']; vwap=s['vwap']; n=len(C)
    z={'symbol':s.get('symbol'),'date':s.get('date'),'time':s['time'],'i':i}

    # Outcome from original arrow, only for descriptive labeling.
    j20=min(n-1,i+20); j60=min(n-1,i+60)
    z['mfe20']=pct(e,max(H[i:j20+1])); z['mfe60']=pct(e,max(H[i:j60+1]))
    z['strong20']=z['mfe20']>=0.50; z['strong60']=z['mfe60']>=1.00

    prior=V[max(0,i-10):i]; prior_avg=mean(prior) if prior else 0.0
    for w in (3,5):
        j=i+w
        confirm=C[j]
        hh,hl=consecutive_hh_hl(H,L,i,j)
        z[f'ret{w}']=pct(e,confirm)
        z[f'hh{w}']=hh; z[f'hl{w}']=hl
        z[f'pullback{w}']=pct(e,min(L[i:j+1]))
        nextvol=mean(V[i+1:j+1]) if j>i else 0.0
        z[f'volpersist{w}']=(nextvol/prior_avg if prior_avg>0 else 0.0)
        z[f'above_vwap{w}']=all(C[k] >= vwap[k] for k in range(i+1,j+1))
        z[f'hist_up_count{w}']=sum(1 for k in range(i+1,j+1) if hist[k] > hist[k-1])
        z[f'hist_accel{w}']=hist[j] > hist[i]

        # Tradable result AFTER waiting w minutes for confirmation.
        end20=min(n-1,j+20); end60=min(n-1,j+60)
        z[f'post_mfe20_{w}']=pct(confirm,max(H[j:end20+1]))
        z[f'post_mae20_{w}']=pct(confirm,min(L[j:end20+1]))
        z[f'post_close20_{w}']=pct(confirm,C[end20])
        z[f'post_mfe60_{w}']=pct(confirm,max(H[j:end60+1]))
    return z


def tests():
    return [
      ('RET3>0',3,lambda x:x['ret3']>0),
      ('RET3>=0.10',3,lambda x:x['ret3']>=0.10),
      ('RET3>=0.20',3,lambda x:x['ret3']>=0.20),
      ('HHHL3>=2',3,lambda x:x['hh3']>=2 and x['hl3']>=2),
      ('PULL3>-0.15',3,lambda x:x['pullback3']>-0.15),
      ('VOLP3>=1.0',3,lambda x:x['volpersist3']>=1.0),
      ('HIST3_ACCEL',3,lambda x:x['hist_accel3']),
      ('VWAP3_HOLD',3,lambda x:x['above_vwap3']),
      ('RET3+.10_HHHL',3,lambda x:x['ret3']>=0.10 and x['hh3']>=2 and x['hl3']>=2),
      ('RET3+.10_HIST',3,lambda x:x['ret3']>=0.10 and x['hist_accel3']),
      ('RET3+.10_HIST_VWAP',3,lambda x:x['ret3']>=0.10 and x['hist_accel3'] and x['above_vwap3']),
      ('RET3+.10_HHHL_HIST',3,lambda x:x['ret3']>=0.10 and x['hh3']>=2 and x['hl3']>=2 and x['hist_accel3']),
      ('RET5>0',5,lambda x:x['ret5']>0),
      ('RET5>=0.20',5,lambda x:x['ret5']>=0.20),
      ('RET5>=0.30',5,lambda x:x['ret5']>=0.30),
      ('HHHL5>=3',5,lambda x:x['hh5']>=3 and x['hl5']>=3),
      ('PULL5>-0.20',5,lambda x:x['pullback5']>-0.20),
      ('VOLP5>=1.0',5,lambda x:x['volpersist5']>=1.0),
      ('HIST5_ACCEL',5,lambda x:x['hist_accel5']),
      ('VWAP5_HOLD',5,lambda x:x['above_vwap5']),
      ('RET5+.20_HIST',5,lambda x:x['ret5']>=0.20 and x['hist_accel5']),
      ('RET5+.20_HIST_VWAP',5,lambda x:x['ret5']>=0.20 and x['hist_accel5'] and x['above_vwap5']),
      ('RET5+.20_HHHL_HIST',5,lambda x:x['ret5']>=0.20 and x['hh5']>=3 and x['hl5']>=3 and x['hist_accel5']),
    ]


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--symbols',default=','.join(DEFAULT_SYMBOLS)); args=ap.parse_args()
    syms=[x.strip().upper() for x in args.symbols.split(',') if x.strip()]
    con=sqlite3.connect(args.db); rows=[]
    for sym in syms:
        dm=load_days(con,sym,args.max_days); ds=sorted(dm); c=0
        for di in range(1,len(ds)):
            ss=raw_signals(dm[ds[di-1]],dm[ds[di]])
            for s in ss:
                s['symbol']=sym; s['date']=ds[di]; rows.append(enrich(s)); c+=1
        print('AUDIT',sym,'DAYS=',max(0,len(ds)-1),'SIGNALS=',c)
    con.close()

    print('\n=== WILLIAMS TREND STRENGTH V2 CAUSAL ===')
    print('RAW_ARROW=Williams CrossUp + RSI2>50. Confirmation uses only next 3m/5m data.')
    print('N=',len(rows))
    if not rows:return
    base20=100*sum(x['strong20'] for x in rows)/len(rows); base60=100*sum(x['strong60'] for x in rows)/len(rows)
    print('BASE_STRONG20=',f'{base20:.2f}%','BASE_STRONG60=',f'{base60:.2f}%')
    ranked=[]
    for name,w,fn in tests():
        z=[x for x in rows if fn(x)]
        if len(z)<30: continue
        s20=100*sum(x['strong20'] for x in z)/len(z); s60=100*sum(x['strong60'] for x in z)/len(z)
        pm=mean([x[f'post_mfe20_{w}'] for x in z]); pa=mean([x[f'post_mae20_{w}'] for x in z]); pc=mean([x[f'post_close20_{w}'] for x in z])
        lift20=s20/base20 if base20 else 0; lift60=s60/base60 if base60 else 0
        score=(lift20+lift60)/2 + max(0,pc)*0.10
        ranked.append((score,name,w,len(z),s20,s60,lift20,lift60,pm,pa,pc))
        print(name,'W=',w,'N=',len(z),'STR20=',f'{s20:.2f}%','LIFT20=',f'{lift20:.2f}x','STR60=',f'{s60:.2f}%','LIFT60=',f'{lift60:.2f}x','POST20_MFE=',f'{pm:.3f}%','POST20_MAE=',f'{pa:.3f}%','POST20_CLOSE=',f'{pc:.3f}%')
    ranked.sort(reverse=True)
    print('\n=== TOP CAUSAL TREND CONFIRMERS ===')
    for rank,r in enumerate(ranked[:12],1):
        score,name,w,n,s20,s60,l20,l60,pm,pa,pc=r
        print(rank,name,'W=',w,'N=',n,'SCORE=',f'{score:.3f}','STR20=',f'{s20:.2f}%','STR60=',f'{s60:.2f}%','LIFT=',f'{(l20+l60)/2:.2f}x','POST20_CLOSE=',f'{pc:.3f}%')

if __name__=='__main__': main()
