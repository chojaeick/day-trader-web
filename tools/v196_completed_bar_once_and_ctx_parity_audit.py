#!/usr/bin/env python3
from pathlib import Path
import sqlite3, json, time, urllib.request, subprocess, re

API='http://127.0.0.1:8000'
DB='/home/ubuntu/day-trader-api/daytrader.db'
K=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
V4=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
print('=== V196 COMPLETED 1M BAR ONCE + CTX PARITY AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('DB=',DB,'KIWOOM=',K.exists(),'V4=',V4.exists())

# 1) Static source audit for completed-bar gating / per-bar state.
for p in (K,V4):
    if not p.exists(): continue
    lines=p.read_text(errors='ignore').splitlines()
    print('--- SOURCE',p.name,'---')
    needles=['completed','last_bar','bar_key','weak_run','frozen','_paper_williams_step','ticks_to_bars','williams_frozen']
    hits=[]
    for i,line in enumerate(lines,1):
        low=line.lower()
        if any(n.lower() in low for n in needles):
            hits.append((i,line.strip()))
    for i,line in hits[-120:]:
        print(f'{i}: {line}')

# 2) Current 19-symbol tick freshness and minute cadence.
frozen=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
con=sqlite3.connect(DB)
now=time.time()
for s in frozen:
    rows=con.execute('select id,ts,price,qty from ticks where symbol=? order by id desc limit 5',(s,)).fetchall()
    print('TICKS',s,rows)

# 3) Poll frozen-paper endpoint several times across >1 minute to detect whether bar advances sanely.
def get_json(path,timeout=20):
    with urllib.request.urlopen(API+path,timeout=timeout) as r:
        return r.status,json.loads(r.read().decode())

snapshots=[]
for idx,delay in enumerate((0,20,20,20)):
    if delay: time.sleep(delay)
    try:
        code,d=get_json('/api/v4/USA/frozen-paper',timeout=20)
        rows=d.get('rows') or []
        by={str(x.get('symbol')):x for x in rows}
        snap={s:{k:by.get(s,{}).get(k) for k in ('bar','ctx','eval_reason','entry','exit','paper_event','ticks')} for s in frozen}
        print('SNAPSHOT',idx,'HTTP',code,'EVAL',d.get('evaluations'),'ERRORS',d.get('errors'),'PAPER_EVENTS',d.get('paper_events'))
        for s in frozen:
            print('ROW',idx,s,snap[s])
        snapshots.append(snap)
    except Exception as e:
        print('SNAPSHOT_ERROR',idx,repr(e))

# 4) Evaluate bar advancement counts. At most one minute step per completed-minute observation is expected.
if len(snapshots)>=2:
    changed={s:0 for s in frozen}
    for a,b in zip(snapshots,snapshots[1:]):
        for s in frozen:
            if a[s].get('bar') != b[s].get('bar'):
                changed[s]+=1
    print('BAR_CHANGE_COUNTS',changed)

# 5) API mode / paper account authority sanity.
for ep in ('/api/v4/runtime-mode','/api/v4/USA/status'):
    try:
        code,d=get_json(ep,timeout=20)
        print('ENDPOINT',ep,'HTTP',code)
        if ep.endswith('runtime-mode'):
            print('RUNTIME_MODE',d)
        else:
            print('USA_STATUS_MODE',d.get('mode'),'SESSION',d.get('session'))
            print('POSITIONS_COUNT',len(d.get('positions') or []),'PAPER_TRADES_COUNT',len(d.get('paper_trades') or []))
            print('PAPER_ACCOUNT',d.get('paper_account'))
    except Exception as e:
        print('ENDPOINT_ERROR',ep,repr(e))

print('NEXT=IF_NO_DUPLICATE_PER_BAR_BEHAVIOR_AND_CTX_ROWS_CURRENT__START/CONTINUE_USA_PAPER; ELSE PATCH_ONLY_THE_CONFIRMED_PARITY_DEFECT')
