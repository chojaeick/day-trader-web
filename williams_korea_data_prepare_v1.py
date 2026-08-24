#!/usr/bin/env python3
import argparse, sqlite3, subprocess, os, re, json, sys
from collections import Counter, defaultdict

DB_DEFAULT="daytrader.db"

def kr_codes(con):
    rows=con.execute("select symbol,count(*) from v4_signal_events where symbol glob '[0-9][0-9][0-9][0-9][0-9][0-9]' group by symbol order by count(*) desc").fetchall()
    return [r[0] for r in rows]

def find_downloader():
    candidates=[
        os.path.expanduser("~/day-trader-api/tools/historical_minute_downloader.py"),
        os.path.expanduser("~/day-trader-api/tools/historical_downloader.py"),
    ]
    for p in candidates:
        if os.path.exists(p): return p
    tools_dir=os.path.expanduser("~/day-trader-api/tools")
    if os.path.isdir(tools_dir):
        for n in os.listdir(tools_dir):
            if n.startswith("historical") and n.endswith("downloader.py"):
                return os.path.join(tools_dir,n)
    return None

def missing(con,codes,max_days):
    out=[]
    for c in codes:
        n=con.execute("select count(distinct trade_date) from historical_minute_bars where symbol=? and interval_min=1",(c,)).fetchone()[0]
        if n<max_days: out.append((c,n))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default=DB_DEFAULT)
    ap.add_argument("--max-symbols",type=int,default=13)
    ap.add_argument("--max-days",type=int,default=20)
    ap.add_argument("--dry-run",action="store_true")
    args=ap.parse_args()

    con=sqlite3.connect(args.db)
    codes=kr_codes(con)[:args.max_symbols]
    print("=== WILLIAMS KOREA DATA PREP V1 ===")
    print("FINDER_CODES=",",".join(codes) if codes else "NONE")
    if not codes:
        print("RESULT=NO_KR_CODES_IN_V4_SIGNAL_EVENTS"); return

    miss=missing(con,codes,args.max_days)
    print("MISSING_OR_INCOMPLETE=",len(miss))
    for c,n in miss: print("CODE",c,"HAVE_DAYS",n)

    dl=find_downloader()
    print("DOWNLOADER=",dl or "NONE")
    if not dl:
        print("RESULT=DOWNLOADER_NOT_FOUND"); return

    if args.dry_run:
        print("DRY_RUN=True")
        for c,_ in miss:
            print("RUN",sys.executable,dl,c)
        return

    # Do not blindly download dates: existing downloader behavior/date semantics vary.
    # Instead print exact next safe commands for the discovered codes so the user can run them.
    print("RESULT=READY")
    print("NOTE=Downloader date arguments differ by script version; inspect --help before bulk download.")
    print("NEXT_COMMAND_1=",f"{sys.executable} {dl} --help")
    print("NEXT_COMMAND_2=",f"python3 williams_korea_validation_v1.py --max-days 135 --max-symbols {args.max_symbols}")

if __name__=="__main__":
    main()
