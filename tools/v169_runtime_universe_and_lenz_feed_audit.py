#!/usr/bin/env python3
"""V169 runtime universe + LENZ feed audit.
Read-only. No strategy/order/service mutation.
"""
from __future__ import annotations
import json, sqlite3, subprocess, urllib.request
from pathlib import Path

BASE='http://127.0.0.1:8000'
DB='/home/ubuntu/day-trader-api/daytrader.db'

def get(path,timeout=5):
    try:
        with urllib.request.urlopen(BASE+path,timeout=timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            try: return r.status,json.loads(raw)
            except Exception: return r.status,raw
    except Exception as e:
        return None,{'error':str(e)}

print('=== V169 RUNTIME UNIVERSE + LENZ FEED AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')

status_code,status=get('/api/v4/USA/status')
finder_code,finder=get('/api/v4/USA/finder')
tracker_code,tracker=get('/api/v4/USA/tracker')
print('HTTP status/finder/tracker=',status_code,finder_code,tracker_code)

positions=(status or {}).get('positions') if isinstance(status,dict) else None
finder_rows=((status or {}).get('finder') or {}).get('rows') if isinstance(status,dict) else None
tracker_rows=((status or {}).get('tracker') or {}).get('rows') if isinstance(status,dict) else None
if not tracker_rows and isinstance(tracker,dict): tracker_rows=tracker.get('rows') or []
if not finder_rows and isinstance(finder,dict): finder_rows=finder.get('rows') or []
positions=positions or []
finder_rows=finder_rows or []
tracker_rows=tracker_rows or []

print('POSITIONS=',[(p.get('symbol'),p.get('qty'),p.get('avg_entry')) for p in positions])
print('FINDER_ROWS=',[(r.get('symbol'),r.get('rank'),r.get('finder_score')) for r in finder_rows])
print('TRACKER_ROWS=',[(r.get('symbol'),r.get('state'),r.get('price')) for r in tracker_rows])

pos_syms={str(p.get('symbol') or '').upper() for p in positions if p.get('symbol')}
find_syms={str(r.get('symbol') or '').upper() for r in finder_rows if r.get('symbol')}
track_syms={str(r.get('symbol') or '').upper() for r in tracker_rows if r.get('symbol')}
print('POSITION_SYMBOLS=',sorted(pos_syms))
print('FINDER_SYMBOLS=',sorted(find_syms))
print('TRACKER_SYMBOLS=',sorted(track_syms))
print('TRACKER_ONLY=',sorted(track_syms-(pos_syms|find_syms)))
print('POSITION_NOT_FINDER=',sorted(pos_syms-find_syms))

con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
cur=con.cursor()
# quote/tick state for relevant symbols
syms=sorted(pos_syms|find_syms|track_syms|{'LENZ','SPCX','SOXS'})
for sym in syms:
    try:
        q=cur.execute('select * from quotes where symbol=? order by rowid desc limit 1',(sym,)).fetchone()
    except Exception:
        q=None
    try:
        n=cur.execute('select count(*) from ticks where symbol=?',(sym,)).fetchone()[0]
        last=cur.execute('select ts,price,qty from ticks where symbol=? order by id desc limit 1',(sym,)).fetchone()
    except Exception as e:
        n=-1; last=None
    print('SYMBOL',sym,'TICKS=',n,'LAST_TICK=',dict(last) if last else None,'QUOTE=',dict(q) if q else None)

# inspect whether LENZ is persisted as an open USA paper/store position or finder residue
for table in ['v4_positions','ranking_snapshots','ranking_archive_rows']:
    try:
        cols=[r[1] for r in cur.execute(f'pragma table_info({table})')]
        if not cols: continue
        if 'symbol' in cols:
            rows=cur.execute(f"select * from {table} where symbol='LENZ' order by rowid desc limit 5").fetchall()
            print('LENZ_TABLE',table,'ROWS=',[dict(r) for r in rows])
    except Exception as e:
        print('LENZ_TABLE',table,'ERR=',repr(e))
con.close()

# recent service logs around LENZ / SPCX / SOXS / subscription/warm failures
try:
    p=subprocess.run(['sudo','journalctl','-u','day-trader-api.service','--since','30 min ago','--no-pager'],capture_output=True,text=True,timeout=20)
    lines=[]
    for ln in (p.stdout or '').splitlines():
        u=ln.upper()
        if any(k in u for k in ['LENZ','SPCX','SOXS','WARM','SUBSCR','WEBSOCKET','SNAPSHOT','QUOTE']):
            lines.append(ln)
    print('=== RECENT FEED/WARM LOGS ===')
    for ln in lines[-120:]: print(ln)
except Exception as e:
    print('JOURNAL_ERR=',repr(e))

print('NEXT=IF_LENZ_POSITION_WITH_ZERO_TICKS_FIX_POSITION_WARMING; IF_FINDER_ZERO/SMALL_FIX_DISCOVERY_UNIVERSE; STRATEGY_UNCHANGED')
