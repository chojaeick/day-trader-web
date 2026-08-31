#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, os, re, time

ROOT=Path('/home/ubuntu/day-trader-api')
LIVE=ROOT/'live_server'
RUN=LIVE/'v22e_us_mock_live.py'
BROKER=LIVE/'kiwoom_us_mock_broker.py'
EVAL=ROOT/'v22e_us_mock_eval.json'
APP=ROOT/'app_v5.py'

KEYS=('order_once','ORDER_ACCEPTED','pending_buy','fill','filled','unfilled','reprice','repric','chase','cancel','replace','limit','bid','ask','quote','retry','poll','ust20','marketable','slippage','tick','STATE_CLEARED_BROKER_FLAT','BUY_CASH_REFRESH_CONFIRMED')

def snippets(p, keys=KEYS, radius=2, max_blocks=120):
    try: lines=p.read_text(encoding='utf-8',errors='replace').splitlines()
    except Exception as e:
        print(f'READ_ERROR {p}: {e!r}'); return
    hits=[]
    for i,x in enumerate(lines):
        lo=x.lower()
        if any(k.lower() in lo for k in keys): hits.append(i)
    merged=[]
    for i in hits:
        a=max(0,i-radius); b=min(len(lines),i+radius+1)
        if merged and a<=merged[-1][1]+1: merged[-1]=(merged[-1][0],max(merged[-1][1],b))
        else: merged.append((a,b))
    for n,(a,b) in enumerate(merged[:max_blocks],1):
        print(f'--- block {n} lines {a+1}-{b} ---')
        for j in range(a,b): print(f'{j+1}: {lines[j]}')

print('===== V22E RUNTIME/BACKUPS =====')
files=sorted(LIVE.glob('v22e_us_mock_live.py*'),key=lambda p:p.stat().st_mtime)
for p in files:
    st=p.stat(); print(f'FILE {p} mtime={time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(st.st_mtime))} size={st.st_size}')
    snippets(p,radius=1,max_blocks=60)

print('===== US BROKER ORDER METHODS =====')
if BROKER.exists(): snippets(BROKER,radius=3,max_blocks=80)
else: print('BROKER_MISSING')

print('===== LIVE EVAL 19 ROW DIAGNOSTIC =====')
try:
    d=json.loads(EVAL.read_text(encoding='utf-8'))
    rows=d if isinstance(d,list) else (d.get('rows') or []) if isinstance(d,dict) else []
    print(f'EVAL_COUNT={len(rows)} source={d.get("source") if isinstance(d,dict) else "LIST"} session={d.get("session") if isinstance(d,dict) else "-"}')
    for n,r in enumerate(rows,1):
        if not isinstance(r,dict): print(n,repr(r)); continue
        sym=r.get('symbol') or r.get('ticker')
        enter=r.get('enter')
        score=next((r.get(k) for k in ('effective_score','entry_score','finder_score','score','power') if r.get(k) is not None),None)
        reason=r.get('reason') or r.get('finder_reason') or r.get('prototype_reason')
        gates={k:r.get(k) for k in ('trend_up','macd_above_signal','macd_rising','macd_accel','rsi_rising','persistence','context_ok') if k in r}
        print(json.dumps({'n':n,'symbol':sym,'enter':enter,'score':score,'reason':reason,'holding':r.get('holding'),'gates':gates},ensure_ascii=False,default=str))
except Exception as e: print('EVAL_READ_ERROR',repr(e))

print('===== V5 USA FINDER/POSITIONS PATHS =====')
if APP.exists():
    lines=APP.read_text(encoding='utf-8',errors='replace').splitlines()
    for a,b,title in [(500,525,'STATUS_HELPERS'),(1008,1025,'FINDER_SOURCE'),(820,895,'POSITIONS_RENDER'),(1175,1195,'TOP_SUMMARY')]:
        print(f'--- {title} {a}-{b} ---')
        for i in range(a-1,min(b,len(lines))): print(f'{i+1}: {lines[i]}')
else: print('APP_MISSING')

print('READ_ONLY=YES')
print('SERVICE_RESTART=NO')
print('ORDER_SENT=NO')
