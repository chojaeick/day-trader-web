#!/usr/bin/env python3
import sqlite3, json, subprocess, re
from collections import Counter
from pathlib import Path

DB='/home/ubuntu/day-trader-api/daytrader.db'
K='/home/ubuntu/day-trader-api/live_server/kiwoom.py'
print('=== V189 F5 TYPE + RAW DELIVERY AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('KIWOOM=',K,'EXISTS=',Path(K).exists())

# Inspect every websocket REG type and extract parser assumptions.
if Path(K).exists():
    lines=Path(K).read_text(encoding='utf-8').splitlines()
    for i,line in enumerate(lines,1):
        if "'type':['" in line or '"type":[' in line or 'def _extract_f5' in line or "row.get('type')" in line:
            print(f'CODE {i}: {line.strip()}')

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
cols=[r['name'] for r in con.execute('pragma table_info(raw_ws)')]
print('RAW_WS_COLS=',cols)
rows=con.execute('select id,payload,ts from raw_ws order by id desc limit 1000').fetchall()
shape=Counter(); data_types=Counter(); items=Counter(); samples=[]
for r in rows:
    try:d=json.loads(r['payload'])
    except Exception:
        shape['NONJSON']+=1; continue
    tr=str(d.get('trnm') or '')
    if tr: shape[tr]+=1
    data=d.get('data') or []
    if isinstance(data,list):
        for x in data:
            if not isinstance(x,dict): continue
            t=str(x.get('type') or '')
            if t: data_types[t]+=1
            item=str(x.get('item') or '')
            if item: items[item]+=1
            if len(samples)<40 and (t or item):
                samples.append({'id':r['id'],'ts':r['ts'],'trnm':tr,'type':t,'item':item,'keys':list((x.get('values') or {}).keys())[:20]})
print('RAW_SHAPE_COUNTS=',dict(shape))
print('DATA_TYPE_COUNTS=',dict(data_types))
print('TOP_ITEMS=',items.most_common(30))
print('SAMPLES_BEGIN')
for s in samples: print(json.dumps(s,ensure_ascii=False))
print('SAMPLES_END')

# Compare raw presence for stale AMD and fresh PLTR across the whole recent window.
for sym in ('AMD','PLTR'):
    hit=0; typec=Counter(); sample=[]
    for r in rows:
        try:d=json.loads(r['payload'])
        except Exception: continue
        for x in (d.get('data') or []):
            if not isinstance(x,dict): continue
            if str(x.get('item') or '').upper()==sym:
                hit+=1; typec[str(x.get('type') or '')]+=1
                if len(sample)<8: sample.append({'id':r['id'],'ts':r['ts'],'type':x.get('type'),'values':x.get('values')})
    print('SYMBOL_RAW',sym,'HITS=',hit,'TYPES=',dict(typec))
    for s in sample: print('RAW_SAMPLE',sym,json.dumps(s,ensure_ascii=False)[:1800])
con.close()

# Recent websocket logs for any type/registration hints.
print('=== RECENT WS LOGS ===')
r=subprocess.run(['journalctl','-u','day-trader-api','--since','45 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
for line in r.stdout.splitlines():
    low=line.lower()
    if any(x in low for x in ['websocket','reg','f5','real','type','실시간']):
        print(line[-1400:])
print('NEXT=IF_PLTR_RAW_TYPE_DIFFERS_FROM_F5_PATCH_REG_OR_PARSER_TO_ACTUAL_TYPE; IF_PLTR_F5_AND_AMD_NONE_THEN_PROBE_OTHER_USA_REALTIME_TYPE_CODES_WITH_STALE/FRESH_AB')
