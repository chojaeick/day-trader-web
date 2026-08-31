#!/usr/bin/env python3
from pathlib import Path
import json, subprocess, time
ROOT=Path('/home/ubuntu/day-trader-api')
LOG=ROOT/'v22e_us_mock_live.jsonl'
EVAL=ROOT/'v22e_us_mock_eval.json'
ACCT=ROOT/'v22e_us_mock_account.json'
print('V86_START=YES')
# give the running service a short window to produce a fresh cycle
try:
    mt=LOG.stat().st_mtime if LOG.exists() else 0
except Exception:
    mt=0
for _ in range(8):
    time.sleep(2)
    try:
        if LOG.exists() and LOG.stat().st_mtime>mt: break
    except Exception: pass

def loadj(p,default):
    try:return json.loads(p.read_text(encoding='utf-8'))
    except Exception:return default

ev=loadj(EVAL,{})
rows=ev if isinstance(ev,list) else (ev.get('rows',[]) if isinstance(ev,dict) else [])
rows=[r for r in rows if isinstance(r,dict)]
rows=sorted(rows,key=lambda r:float(r.get('score') or 0),reverse=True)
print('LIVE_EVAL_ROWS='+str(len(rows)))
print('TOP8='+json.dumps([[r.get('symbol'),r.get('score'),r.get('enter'),r.get('reason')] for r in rows[:8]],ensure_ascii=False))
acct=loadj(ACCT,{})
hs=acct.get('holdings',[]) if isinstance(acct,dict) else []
print('BROKER_HOLDINGS='+json.dumps([[h.get('symbol'),h.get('qty'),h.get('avg'),h.get('price')] for h in hs if isinstance(h,dict)],ensure_ascii=False))
print('ORDERABLE_CASH='+str(acct.get('orderable_cash') if isinstance(acct,dict) else None))
# print recent engine decision/order events only
wanted=('V85_ACTIVE_ENTRY','LIVE_CAPITAL_SIZE','ORDER_ATTEMPT','ORDER_ACCEPTED','ORDER_FAILED','ORDER_CANCEL','STALE_ORDER','BUY_','SELL_','HOLDING_DATA_GAP','HEARTBEAT','ACCOUNT_READ')
lines=[]
if LOG.exists():
    try: lines=LOG.read_text(encoding='utf-8',errors='replace').splitlines()[-300:]
    except Exception: lines=[]
print('=== RECENT_V22E_EVENTS ===')
count=0
for ln in lines:
    if any(k in ln for k in wanted):
        print(ln);count+=1
print('RECENT_EVENT_LINES='+str(count))
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
print('READ_ONLY=YES')
print('ORDER_SENT_BY_V86=NO')
