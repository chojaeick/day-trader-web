#!/usr/bin/env python3
import argparse, sqlite3, re

KR_RE = re.compile(r'^\d{6}$')

def pct(a,b):
    return (b/a-1.0)*100.0 if a else 0.0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--db',default='daytrader.db')
    ap.add_argument('--max-days',type=int,default=20)
    ap.add_argument('--max-symbols',type=int,default=13)
    args=ap.parse_args()
    con=sqlite3.connect(args.db)
    syms=[r[0] for r in con.execute("select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc").fetchall() if KR_RE.match(str(r[0] or ''))][:args.max_symbols]
    print('=== KOREA MINUTE DATA INTEGRITY AUDIT V1 ===')
    print('SYMBOLS=',','.join(syms))
    total_days=bad_ohlc=dup_days=weird_time_days=0
    big=[]
    for s in syms:
        ds=[r[0] for r in con.execute("select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",(s,args.max_days)).fetchall()]
        ds=sorted(ds)
        print(f'\n--- {s} DAYS={len(ds)} ---')
        for d in ds:
            rows=con.execute("select ts,et_time,open,high,low,close,volume,source from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",(s,d)).fetchall()
            if not rows: continue
            total_days += 1
            uniq=len(set(str(r[0]) for r in rows))
            if uniq != len(rows): dup_days += 1
            first_t=str(rows[0][1] or ''); last_t=str(rows[-1][1] or '')
            if not first_t.startswith('09:') or not last_t.startswith('15:'):
                weird_time_days += 1
            O=float(rows[0][2]); H=max(float(r[3]) for r in rows); L=min(float(r[4]) for r in rows); C=float(rows[-1][5])
            bad=0; max_jump=0.0; zero_vol=0; prev=None
            for r in rows:
                o,h,l,c,v=map(float,(r[2],r[3],r[4],r[5],r[6]))
                if h < max(o,c,l) or l > min(o,c,h) or min(o,h,l,c) <= 0: bad += 1
                if v == 0: zero_vol += 1
                if prev and prev>0: max_jump=max(max_jump,abs(pct(prev,c)))
                prev=c
            bad_ohlc += bad
            o2h=pct(O,H); o2c=pct(O,C); l2h=pct(L,H)
            if o2h>=5.0: big.append((o2h,s,d,O,H,L,C))
            print(f'{d} BARS={len(rows)} UNIQUE_TS={uniq} TIME={first_t}->{last_t} SRC={rows[0][7]} O={O:.4f} H={H:.4f} L={L:.4f} C={C:.4f} O2H={o2h:.2f}% O2C={o2c:.2f}% L2H={l2h:.2f}% MAX1M={max_jump:.2f}% ZERO_VOL={100*zero_vol/len(rows):.1f}% BAD_OHLC={bad}')
    print('\n=== SUMMARY ===')
    print('TOTAL_SYMBOL_DAYS=',total_days)
    print('BAD_OHLC_ROWS=',bad_ohlc)
    print('DUPLICATE_TS_DAYS=',dup_days)
    print('WEIRD_TIME_DAYS=',weird_time_days)
    print('OPEN_TO_HIGH_GE5_DAYS=',len(big))
    print('\n=== TOP OPEN->HIGH MOVES ===')
    for o2h,s,d,O,H,L,C in sorted(big,reverse=True)[:40]:
        print(f'{s} {d} O2H={o2h:.2f}% O={O:.4f} H={H:.4f} L={L:.4f} C={C:.4f} O2C={pct(O,C):.2f}%')
    print('\n=== SAMPLE RAW BARS: 005930 20260818 ===')
    for r in con.execute("select et_time,open,high,low,close,volume,ts,source from historical_minute_bars where symbol='005930' and trade_date='20260818' and interval_min=1 order by et_time limit 20").fetchall():
        print(r)
    con.close()

if __name__=='__main__': main()
