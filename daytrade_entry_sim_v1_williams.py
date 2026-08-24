#!/usr/bin/env python3
import argparse, math, re, sqlite3, statistics
from collections import defaultdict
from pathlib import Path

HORIZONS=(1,3,5,10,20)


def fnum(x):
    try: return float(x)
    except Exception: return None


def rsi_wilder(closes, period=2):
    out=[None]*len(closes)
    if len(closes)<=period: return out
    gains=[]; losses=[]
    for i in range(1, period+1):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0.0)); losses.append(max(-d,0.0))
    ag=sum(gains)/period; al=sum(losses)/period
    out[period]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    for i in range(period+1,len(closes)):
        d=closes[i]-closes[i-1]
        g=max(d,0.0); l=max(-d,0.0)
        ag=(ag*(period-1)+g)/period
        al=(al*(period-1)+l)/period
        out[i]=100.0 if al==0 else 100.0-(100.0/(1.0+ag/al))
    return out


def pct(a,b):
    if a is None or b in (None,0): return None
    return (a/b-1.0)*100.0


def avg(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else None


def med(xs):
    xs=[x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def find_db(explicit=None):
    cands=[]
    if explicit: cands.append(Path(explicit))
    cands += [Path('daytrader.db'),Path('/home/ubuntu/day-trader-api/daytrader.db')]
    for p in cands:
        if p.exists(): return p
    raise SystemExit('DB_NOT_FOUND')


def table_cols(c, table):
    return [r[1] for r in c.execute(f'pragma table_info({table})')]


def audit(c):
    tabs=[r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
    print('=== DATA AUDIT ===')
    print('TABLE_COUNT=',len(tabs))
    for t in ('historical_minute_bars','ticks','raw_ws','daily_history'):
        if t in tabs:
            try: n=c.execute(f'select count(*) from {t}').fetchone()[0]
            except Exception: n='ERR'
            print(f'{t}_ROWS=',n)
    if 'historical_minute_bars' not in tabs:
        return False
    cols=table_cols(c,'historical_minute_bars')
    print('HIST_COLS=',','.join(cols))
    try:
        print('HIST_DATE_RANGE=',c.execute('select min(trade_date),max(trade_date) from historical_minute_bars').fetchone())
        syms=c.execute('select symbol,count(*),count(distinct trade_date) from historical_minute_bars group by symbol order by count(*) desc limit 30').fetchall()
        print('HIST_SYMBOL_SAMPLE=',syms)
    except Exception as e:
        print('AUDIT_ERR=',repr(e))
    return True


def load_rows(c, max_symbols=50, max_days=20):
    cols=table_cols(c,'historical_minute_bars')
    need={'symbol','trade_date','open','high','low','close','volume'}
    if not need.issubset(cols):
        raise SystemExit('HIST_SCHEMA_MISSING:'+','.join(sorted(need-set(cols))))
    tcol='et_time' if 'et_time' in cols else ('ts' if 'ts' in cols else None)
    if not tcol: raise SystemExit('NO_TIME_COLUMN')
    scol='session' if 'session' in cols else None
    # KRX codes are normally six characters (digits or digit/letter mixtures).
    all_syms=[r[0] for r in c.execute('select distinct symbol from historical_minute_bars')]
    krx=[s for s in all_syms if s and re.fullmatch(r'[0-9A-Z]{6}',str(s).upper())]
    if not krx:
        print('NO_KOREA_LIKE_SYMBOLS_IN_HISTORICAL_MINUTE_BARS')
        return []
    # Prefer symbols with the most date coverage.
    cov=[]
    for s in krx:
        n=c.execute('select count(distinct trade_date) from historical_minute_bars where symbol=?',(s,)).fetchone()[0]
        cov.append((n,s))
    syms=[s for _,s in sorted(cov,reverse=True)[:max_symbols]]
    qmarks=','.join('?'*len(syms))
    dates=[r[0] for r in c.execute(f'select distinct trade_date from historical_minute_bars where symbol in ({qmarks}) order by trade_date desc',syms).fetchall()[:max_days+2]]
    if not dates: return []
    dates=sorted(dates)
    qd=','.join('?'*len(dates))
    select=f"symbol,trade_date,{tcol},open,high,low,close,volume"+(",session" if scol else "")
    sql=f'select {select} from historical_minute_bars where symbol in ({qmarks}) and trade_date in ({qd}) order by symbol,trade_date,{tcol}'
    raw=c.execute(sql,syms+dates).fetchall()
    out=[]
    for r in raw:
        d={'symbol':str(r[0]),'date':str(r[1]),'time':str(r[2]),'open':fnum(r[3]),'high':fnum(r[4]),'low':fnum(r[5]),'close':fnum(r[6]),'volume':fnum(r[7]) or 0.0,'session':str(r[8]) if scol else ''}
        if None in (d['open'],d['high'],d['low'],d['close']): continue
        if scol and d['session'] and d['session'].upper() not in {'REGULAR','REG','RTH'}: continue
        out.append(d)
    print('KRX_LIKE_SYMBOLS=',len(krx),'SELECTED_SYMBOLS=',len(syms),'SELECTED_DATES=',len(dates),'ROWS=',len(out))
    return out


def simulate(rows):
    bysym=defaultdict(list)
    for r in rows: bysym[r['symbol']].append(r)
    signals=[]
    for sym,bars in bysym.items():
        bars.sort(key=lambda x:(x['date'],x['time']))
        closes=[b['close'] for b in bars]
        rsis=rsi_wilder(closes,2)
        bydate=defaultdict(list)
        for i,b in enumerate(bars): bydate[b['date']].append(i)
        dates=sorted(bydate)
        for di in range(1,len(dates)):
            prev_date,day=dates[di-1],dates[di]
            pidx=bydate[prev_date]; didx=bydate[day]
            if len(pidx)<10 or len(didx)<5: continue
            prev_hi=max(bars[i]['high'] for i in pidx)
            prev_lo=min(bars[i]['low'] for i in pidx)
            day_open=bars[didx[0]]['open']
            trigger=day_open+(prev_hi-prev_lo)*0.5
            for j,pos in enumerate(didx):
                if j==0: continue
                prior=bars[didx[j-1]]['close']; cur=bars[pos]['close']; rsi=rsis[pos]
                cross=(prior<=trigger and cur>trigger)
                if not cross or rsi is None or rsi<=50: continue
                entry=cur
                rec={'symbol':sym,'date':day,'time':bars[pos]['time'],'entry':entry,'trigger':trigger,'rsi2':rsi}
                for h in HORIZONS:
                    k=j+h
                    rec[f'r{h}']=pct(bars[didx[k]]['close'],entry) if k<len(didx) else None
                end=min(len(didx)-1,j+20)
                future=[bars[didx[k]] for k in range(j+1,end+1)]
                rec['mfe20']=max((pct(x['high'],entry) for x in future),default=None)
                rec['mae20']=min((pct(x['low'],entry) for x in future),default=None)
                signals.append(rec)
    return signals


def report(signals):
    print('\n=== WILLIAMS ENTRY-ONLY SIM V1 ===')
    print('RULE=CrossUp(close, day_open + 0.5*(prev_day_high-prev_day_low)) AND RSI(2)>50')
    print('SIGNALS=',len(signals))
    if not signals:
        print('RESULT=NO_SIGNALS_OR_INSUFFICIENT_KOREA_HISTORY')
        return
    print('SYMBOLS=',len(set(s['symbol'] for s in signals)),'DATES=',len(set(s['date'] for s in signals)))
    for h in HORIZONS:
        vals=[s[f'r{h}'] for s in signals if s[f'r{h}'] is not None]
        wins=sum(1 for x in vals if x>0)
        print(f'R{h}M n={len(vals)} avg={avg(vals):.4f}% median={med(vals):.4f}% win={wins/len(vals)*100:.1f}%' if vals else f'R{h}M n=0')
    mfe=[s['mfe20'] for s in signals if s['mfe20'] is not None]
    mae=[s['mae20'] for s in signals if s['mae20'] is not None]
    print(f'MFE20 avg={avg(mfe):.4f}% median={med(mfe):.4f}%' if mfe else 'MFE20 n=0')
    print(f'MAE20 avg={avg(mae):.4f}% median={med(mae):.4f}%' if mae else 'MAE20 n=0')
    print('\nTOP_SAMPLE')
    for s in signals[:20]:
        print(s['date'],s['time'],s['symbol'],f"entry={s['entry']:.4f}",f"trigger={s['trigger']:.4f}",f"rsi2={s['rsi2']:.2f}",f"r5={s['r5']}",f"r10={s['r10']}",f"mfe20={s['mfe20']}",f"mae20={s['mae20']}")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db')
    ap.add_argument('--max-symbols',type=int,default=50)
    ap.add_argument('--max-days',type=int,default=20)
    args=ap.parse_args()
    db=find_db(args.db)
    print('DB=',db)
    c=sqlite3.connect(str(db))
    if not audit(c): raise SystemExit('NO_HISTORICAL_MINUTE_TABLE')
    rows=load_rows(c,args.max_symbols,args.max_days)
    report(simulate(rows))

if __name__=='__main__': main()
