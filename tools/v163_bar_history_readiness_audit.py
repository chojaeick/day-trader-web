#!/usr/bin/env python3
"""V163 diagnose USA frozen live bar-history readiness.
READ ONLY: no strategy/order/service mutation.
Checks live endpoints plus SQLite tick history per currently tracked USA symbol.
"""
from __future__ import annotations
import json, sqlite3, time, urllib.request
from pathlib import Path

BASE='http://127.0.0.1:8000'
DB=Path('/home/ubuntu/day-trader-api/daytrader.db')

def get(path,timeout=5):
    t=time.time()
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            try:d=json.loads(raw)
            except Exception:d=raw
            return r.status,round(time.time()-t,3),None,d
    except Exception as e:
        return None,round(time.time()-t,3),str(e),None

def collect_rows(d):
    if not isinstance(d,dict): return []
    rows=[]
    if isinstance(d.get('rows'),list): rows += d['rows']
    tr=d.get('tracker')
    if isinstance(tr,dict) and isinstance(tr.get('rows'),list): rows += tr['rows']
    return rows

print('=== V163 USA FROZEN BAR HISTORY READINESS AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

allrows=[]
for ep in ['/api/v4/USA/status','/api/v4/USA/tracker','/api/v4/USA/finder']:
    c,s,e,d=get(ep,5)
    print('ENDPOINT',ep,'HTTP=',c,'SEC=',s,'ERR=',e)
    if isinstance(d,dict):
        print('session=',d.get('session'),'updated_at=',d.get('updated_at'))
        rr=collect_rows(d)
        print('ROWS=',len(rr))
        allrows += rr

syms=[]
for r in allrows:
    if isinstance(r,dict):
        s=str(r.get('symbol') or '').upper()
        if s and s not in syms: syms.append(s)
print('UNIQUE_RUNTIME_SYMBOLS=',syms)

con=sqlite3.connect(str(DB),timeout=2)
cur=con.cursor()
# discover ticks schema safely
cols=[x[1] for x in cur.execute("pragma table_info(ticks)").fetchall()]
print('TICKS_COLUMNS=',cols)
# symbol/time column names in this DB
symcol='symbol' if 'symbol' in cols else None
timecol=next((x for x in ('ts','timestamp','time','datetime','created_at') if x in cols),None)
print('TICKS_SYMBOL_COL=',symcol,'TIME_COL=',timecol)

# If runtime rows are empty, use most recent USA snapshot symbols as fallback evidence only.
if not syms:
    try:
        q="select distinct symbol from v4_tracker_snapshots where market='USA' order by ts desc limit 10"
        syms=[str(x[0]).upper() for x in cur.execute(q).fetchall() if x and x[0]]
        print('FALLBACK_RECENT_SNAPSHOT_SYMBOLS=',syms)
    except Exception as e:
        print('FALLBACK_SYMBOL_QUERY_ERROR=',e)

ready25=0
for sym in syms[:20]:
    cnt=0; first=None; last=None
    try:
        if symcol:
            cnt=cur.execute(f"select count(*) from ticks where {symcol}=?",(sym,)).fetchone()[0]
            if timecol:
                first,last=cur.execute(f"select min({timecol}),max({timecol}) from ticks where {symcol}=?",(sym,)).fetchone()
    except Exception as e:
        print('TICK_QUERY_ERROR',sym,e)
    # 25 raw ticks is only a lower bound; engine aggregates to 1m bars. Also inspect tracker row fields when present.
    r=next((x for x in allrows if isinstance(x,dict) and str(x.get('symbol') or '').upper()==sym),{})
    integ=(r.get('data_integrity') or {}) if isinstance(r,dict) else {}
    bars1=integ.get('bars_1m') or r.get('bars_1m') or (r.get('entry_gate') or {}).get('bars_1m')
    ctx=r.get('williams_frozen_ctx') if isinstance(r,dict) else None
    ev=r.get('williams_frozen_eval') if isinstance(r,dict) else None
    if isinstance(bars1,(int,float)) and bars1>=25: ready25+=1
    print('SYMBOL',sym,'TICKS=',cnt,'FIRST=',first,'LAST=',last,'BARS1=',bars1,'CTX=',bool(ctx),'EVAL_REASON=',(ev or {}).get('reason') if isinstance(ev,dict) else None)

# DB recency / tracker snapshot state
try:
    n,last=cur.execute("select count(*),max(ts) from v4_tracker_snapshots where market='USA'").fetchone()
    print('USA_SNAPSHOTS=',n,'LAST=',last)
except Exception as e: print('SNAPSHOT_QUERY_ERROR=',e)
con.close()

print('RUNTIME_ROWS_PRESENT=',bool(allrows))
print('SYMBOLS_WITH_BARS1_GE25=',ready25)
print('DIAGNOSIS=' + ('RUNTIME_ROWS_NOT_WARM_YET' if not allrows else 'ROWS_PRESENT_CHECK_BAR_COUNTS_AND_CTX'))
print('NEXT=USE_OUTPUT_TO_FIX_ONLY_FEED_READINESS_OR_CTX_INPUT_GAP; FROZEN_STRATEGY_UNCHANGED')
