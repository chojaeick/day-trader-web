#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, sqlite3

print('=== V177 WS REG ACK + SEPARATE SESSION FEASIBILITY AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
for p in ['/home/ubuntu/day-trader-api/live_server/kiwoom.py','/home/ubuntu/day-trader-api/live_server/api.py']:
    q=Path(p); print('FILE',p,'EXISTS=',q.exists())
    if not q.exists(): continue
    lines=q.read_text(encoding='utf-8').splitlines()
    pats=['websocket','trnm','REG','PING','recv','send','LOGIN','refresh','_extract_f5','ws_url','create_task','websocket_forever']
    hits=[i for i,l in enumerate(lines,1) if any(x.lower() in l.lower() for x in pats)]
    print('HITS=',hits[-120:])
    # focus around websocket loop
    for i in hits[-50:]:
        if 1880 <= i <= 1985 or 'websocket' in lines[i-1].lower() or '_extract_f5' in lines[i-1]:
            a=max(1,i-4); b=min(len(lines),i+8)
            print(f'--- CONTEXT {a}:{b} ---')
            for n in range(a,b+1): print(f'{n}: {lines[n-1]}')

# recent websocket logs including possible ack/error codes
print('=== RECENT WS LOGS ===')
r=subprocess.run(['journalctl','-u','day-trader-api','--since','20 minutes ago','--no-pager'],capture_output=True,text=True,timeout=20)
for line in r.stdout.splitlines():
    low=line.lower()
    if any(k in low for k in ['websocket','trnm','return_code','return_msg','login','reg','ping','disconnect','closed','error']):
        print(line[-1600:])

# inspect raw_ws latest records schema/content previews, without assuming symbol text is literal
con=sqlite3.connect('/home/ubuntu/day-trader-api/daytrader.db'); con.row_factory=sqlite3.Row
try:
    cols=[x['name'] for x in con.execute('pragma table_info(raw_ws)').fetchall()]
    print('RAW_WS_COLS=',cols)
    rows=con.execute('select * from raw_ws order by rowid desc limit 20').fetchall()
    print('RAW_WS_ROWS=',len(rows))
    for x in rows[:10]:
        d=dict(x)
        for k,v in list(d.items()):
            if isinstance(v,str) and len(v)>600:d[k]=v[:600]+'...'
        print('RAW=',d)
except Exception as e: print('RAW_WS_ERR=',repr(e))
con.close()
print('NEXT=IF_REG_ACK_HAS_LIMIT/ERROR_USE_SEPARATE_FROZEN_WS_SESSION; IF_ACK_OK_BUT_NO_F5_TEST_DEDICATED_FROZEN19_WS_SESSION; STRATEGY_UNCHANGED')
