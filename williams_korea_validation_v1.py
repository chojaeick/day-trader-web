#!/usr/bin/env python3
import argparse, sqlite3, re, statistics, math
from collections import defaultdict

KR_RE = re.compile(r'^\d{6}$')


def table_exists(con, name):
    return con.execute("select 1 from sqlite_master where type='table' and name=?", (name,)).fetchone() is not None


def cols(con, table):
    try:
        return [r[1] for r in con.execute(f'pragma table_info({table})').fetchall()]
    except Exception:
        return []


def ema(vals, span):
    if not vals: return []
    a = 2.0/(span+1.0); out=[float(vals[0])]
    for v in vals[1:]: out.append(a*float(v)+(1-a)*out[-1])
    return out


def rsi(vals, period=2):
    n=len(vals); out=[None]*n
    if n < period+2: return out
    gains=[]; losses=[]
    for i in range(1, period+1):
        d=vals[i]-vals[i-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100-(100/(1+ag/al))
    for i in range(period+1,n):
        d=vals[i]-vals[i-1]; g=max(d,0); l=max(-d,0)
        ag=(ag*(period-1)+g)/period; al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100-(100/(1+ag/al))
    return out


def cci(highs,lows,closes,period=20):
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]; out=[None]*len(tp)
    for i in range(period-1,len(tp)):
        w=tp[i-period+1:i+1]; ma=sum(w)/period; md=sum(abs(x-ma) for x in w)/period
        out[i]=0.0 if md==0 else (tp[i]-ma)/(0.015*md)
    return out


def pct(a,b): return (b/a-1)*100 if a else 0.0


def load_days(con, symbol, max_days):
    ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(symbol,max_days+1)).fetchall()]
    ds=sorted(ds); out={}
    for d in ds:
        rows=con.execute("select et_time,open,high,low,close,volume from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(symbol,d)).fetchall()
        if len(rows)>=20: out[d]=rows
    return out


def find_raw_signals(prev, cur):
    if len(prev)<20 or len(cur)<10: return []
    ph=max(float(r[2]) for r in prev); pl=min(float(r[3]) for r in prev)
    op=float(cur[0][1]); trig=op+0.5*(ph-pl)
    highs=[float(r[2]) for r in cur]; lows=[float(r[3]) for r in cur]; closes=[float(r[4]) for r in cur]
    r2=rsi(closes,2)
    out=[]
    for i in range(2, len(cur)-6):
        if closes[i-1] <= trig < closes[i] and r2[i] is not None and r2[i] > 50:
            out.append((i, closes, highs, lows))
    return out


def strong5_entries(prev, cur):
    z=[]
    for i,closes,highs,lows in find_raw_signals(prev,cur):
        j=i+5
        if j < len(closes) and pct(closes[i], closes[j]) >= 0.30:
            z.append((j, closes, highs, lows))
    return z


def exit_hybrid_lock_ind(entry_i, closes, highs, lows):
    # Fixed representative HYBRID_LOCK_IND behavior used for Korea validation:
    # protect profit after extension, otherwise allow indicator-style breathing room
    c20=cci(highs,lows,closes,20)
    e12=ema(closes,12); e26=ema(closes,26); macd=[a-b for a,b in zip(e12,e26)]; sig=ema(macd,9)
    entry=closes[entry_i]; peak=entry; armed=False
    for i in range(entry_i+1, len(closes)):
        peak=max(peak, highs[i])
        peak_ret=pct(entry, peak)
        if peak_ret >= 0.50: armed=True
        cci_down = c20[i] is not None and c20[i-1] is not None and c20[i] < c20[i-1]
        weak = macd[i] < sig[i] and cci_down
        if armed:
            trail = (closes[i]/peak-1)*100
            if trail <= -0.30 or weak:
                return i
        elif weak and i-entry_i >= 3:
            return i
    return len(closes)-1


def metrics(trades):
    if not trades: return None
    rs=[x['ret'] for x in trades]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    gp=sum(wins); gl=-sum(losses); pf=(gp/gl if gl>0 else (999.0 if gp>0 else 0.0))
    eq=0.0; peak=0.0; mdd=0.0
    for x in rs:
        eq+=x; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    return {'n':len(rs),'avg':statistics.fmean(rs),'win':100*len(wins)/len(rs),'pf':pf,'mdd':mdd}


def show(label,z):
    if not z: print(label,'N=0'); return
    print(label,'N=',z['n'],'AVG=',f"{z['avg']:.4f}%",'WIN=',f"{z['win']:.2f}%",'PF=',f"{z['pf']:.3f}",'MDD=',f"{z['mdd']:.4f}%")


def finder_audit(con):
    candidates=[]
    for t in ('ranking_snapshots','ranking_archive_rows','ranking_rows','v4_signal_events'):
        if not table_exists(con,t): continue
        cc=cols(con,t)
        symcol=next((c for c in cc if c.lower() in ('symbol','code','stock_code')),None)
        datecol=next((c for c in cc if c.lower() in ('trade_date','date','snapshot_date')),None)
        if symcol:
            try:
                q=f"select {symcol}" + (f",{datecol}" if datecol else '') + f" from {t} order by rowid desc limit 5000"
                rows=con.execute(q).fetchall()
                kr=[r for r in rows if r and KR_RE.match(str(r[0] or ''))]
                print('FINDER_TABLE',t,'ROWS_SAMPLE=',len(rows),'KR_CODE_ROWS=',len(kr),'COLS=',','.join(cc))
                for r in kr[:20]: candidates.append(str(r[0]))
            except Exception as e:
                print('FINDER_TABLE',t,'ERROR=',repr(e))
    return candidates


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',default='daytrader.db'); ap.add_argument('--max-days',type=int,default=135); ap.add_argument('--max-symbols',type=int,default=50); args=ap.parse_args()
    con=sqlite3.connect(args.db)
    print('=== WILLIAMS KOREA VALIDATION V1 ===')
    print('DB=',args.db)
    print('RULE_FIXED=Williams CrossUp + RSI2>50; confirm +0.30% after 5m; HYBRID_LOCK_IND style exit')

    if not table_exists(con,'historical_minute_bars'):
        print('RESULT=NO_HISTORICAL_MINUTE_BARS_TABLE'); return

    hc=cols(con,'historical_minute_bars'); print('HIST_COLS=',','.join(hc))
    syms=con.execute("select symbol,count(*) rows,count(distinct trade_date) days,min(trade_date),max(trade_date) from historical_minute_bars where interval_min=1 group by symbol order by rows desc").fetchall()
    kr=[r for r in syms if KR_RE.match(str(r[0] or ''))]
    print('ALL_SYMBOLS=',len(syms),'KOREA_LIKE_SYMBOLS=',len(kr))
    for r in kr[:30]: print('KR_HIST',r)

    finder_syms=finder_audit(con)
    if finder_syms:
        fs=set(finder_syms); kr.sort(key=lambda r:(str(r[0]) not in fs,-int(r[2] or 0),-int(r[1] or 0)))
        print('FINDER_CODE_UNIQUE=',len(fs))

    if not kr:
        print('RESULT=NO_KOREA_1M_HISTORY')
        print('NEXT=Need historical_minute_bars rows for six-digit KRX symbols before causal simulation. No download performed by this audit.')
        con.close(); return

    selected=[str(r[0]) for r in kr[:args.max_symbols]]
    raw=[]
    for s in selected:
        dm=load_days(con,s,args.max_days); ds=sorted(dm); n=0
        for di in range(1,len(ds)):
            for ei,closes,highs,lows in strong5_entries(dm[ds[di-1]],dm[ds[di]]):
                xi=exit_hybrid_lock_ind(ei,closes,highs,lows)
                raw.append({'symbol':s,'date':str(ds[di]),'ret':pct(closes[ei],closes[xi])}); n+=1
        print('AUDIT',s,'DAYS=',max(0,len(ds)-1),'STRONG5_TRADES=',n)

    dates=sorted(set(x['date'] for x in raw)); split=len(dates)//2
    isd=set(dates[:split]); oosd=set(dates[split:])
    print('\nDATE_RANGE=',(dates[0] if dates else None,dates[-1] if dates else None),'UNIQUE_DATES=',len(dates),'IS=',len(isd),'OOS=',len(oosd))
    allz=metrics(raw); isz=metrics([x for x in raw if x['date'] in isd]); oosz=metrics([x for x in raw if x['date'] in oosd])
    show('ALL',allz); show('IS ',isz); show('OOS',oosz)
    passed=bool(oosz and oosz['n']>=30 and oosz['avg']>0 and oosz['pf']>=1.5 and oosz['mdd']>-15)
    print('OOS_PASS=',passed)
    print('NOTE=If Finder history is present it is prioritized in symbol selection; this V1 does not yet reconstruct exact historical TOP5 membership minute-by-minute.')
    con.close()

if __name__=='__main__': main()
