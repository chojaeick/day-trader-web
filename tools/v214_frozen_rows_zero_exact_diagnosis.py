#!/usr/bin/env python3
from pathlib import Path
import subprocess, json, urllib.request, time, re
API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIO=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
DBP=Path('/home/ubuntu/day-trader-api/live_server/db.py')
print('=== V214 FROZEN ROWS ZERO EXACT DIAGNOSIS ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE')
for p in (API,KIO,DBP):
    if not p.exists(): raise SystemExit(f'NOT_FOUND {p}')
api=API.read_text(errors='ignore'); kio=KIO.read_text(errors='ignore'); dbs=DBP.read_text(errors='ignore')
# print exact frozen loop body and relevant queue/flush methods
for name,src,pat in [
 ('FROZEN_LOOP',api,'async def frozen_usa_paper_forever():'),
 ('FLUSH',kio,'async def _v212_flush_tick_buffer'),
 ('QUEUE',kio,'def _v212_queue_tick'),
 ('DB_BATCH',dbs,'def add_ticks_batch')]:
    p=src.find(pat)
    print(name,'POS=',p)
    if p>=0:
        print(src[p:p+6500])

# Endpoint state
BASE='http://127.0.0.1:8000'
def get(path,t=5):
    try:
        with urllib.request.urlopen(BASE+path,timeout=t) as f:
            raw=f.read().decode(errors='ignore')
            try:return f.status,json.loads(raw)
            except:return f.status,raw
    except Exception as e:return 0,repr(e)
for ep in ['/api/v4/runtime-mode','/api/v4/USA/frozen-paper']:
    code,body=get(ep,5); print('EP',ep,'HTTP',code,'BODY',body)

# DB exact latest tick counts/freshness for frozen19
frozen=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','PLTR','QQQ','SMCI','SMH','SOXL','SOXS','SPY','SQQQ','TQQQ','TSM']
script="""
from live_server.config import Settings
from live_server.db import DB
s=Settings(); db=DB(s.db_path)
syms=%r
for sym in syms:
    rows=db.ticks(sym,3)
    print(sym,'N3',len(rows),'LAST',rows[-1] if rows else None)
""" % frozen
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-c',script],cwd='/home/ubuntu/day-trader-api',capture_output=True,text=True)
print('DB_TICK_CHECK_RC=',r.returncode); print(r.stdout); print(r.stderr)

# journal only current boot, relevant frozen/batch/ws errors
j=subprocess.run(['journalctl','-u','day-trader-api.service','-b','--no-pager'],capture_output=True,text=True).stdout
for line in j.splitlines():
    low=line.lower()
    if any(x in low for x in ['frozen','v213','batch flush','websocket live','traceback','error']):
        print('J',line)
print('NEXT=PATCH_ONLY_EXACT_REASON_ROWS_ZERO; CPU_TUNING_DONE')
