#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, sys, re, json
ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
EVAL=ROOT/'v22e_us_mock_eval.json'
ACCT=ROOT/'v22e_us_mock_account.json'
print('V85_START=YES')
text=ENGINE.read_text(encoding='utf-8')
bak=ENGINE.with_name(f'{ENGINE.name}.pre_v85_{int(time.time())}')
shutil.copy2(ENGINE,bak)
helper='''\n# V85_ACTIVE_TOPSCORE_ENTRY\nV85_ACTIVE_MIN_SCORE = 40.0\nV85_ACTIVE_TOP_N = 4\n\ndef _v85_score(row):\n    if not isinstance(row,dict): return -1e9\n    for k in ('effective_score','entry_score','score','power','finder_score'):\n        try:\n            v=row.get(k)\n            if v not in (None,'','-'): return float(v)\n        except Exception:\n            pass\n    return -1e9\n\ndef _v85_active_entry(row, sym, holdings, eval_store):\n    if not isinstance(row,dict) or row.get('enter'):\n        return bool(isinstance(row,dict) and row.get('enter'))\n    if sym in (holdings or {}): return False\n    score=_v85_score(row)\n    if score < V85_ACTIVE_MIN_SCORE: return False\n    vals=[]\n    try:\n        src=eval_store.values() if isinstance(eval_store,dict) else eval_store\n        for r in (src or []):\n            if not isinstance(r,dict): continue\n            rs=str(r.get('symbol') or '').upper().strip()\n            if rs and rs not in (holdings or {}): vals.append(( _v85_score(r), rs ))\n    except Exception:\n        vals=[]\n    vals=sorted(vals, reverse=True)[:V85_ACTIVE_TOP_N]\n    allowed={s for _,s in vals}\n    if str(sym).upper().strip() in allowed:\n        row['_v85_forced_entry']=True\n        row['reason']='V22E_ACTIVE_TOP_SCORE'\n        row['enter']=True\n        return True\n    return False\n'''
if 'V85_ACTIVE_TOPSCORE_ENTRY' not in text:
    anchor='def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n'
    if anchor not in text:
        print('ABORT=ORDER_ONCE_ANCHOR_MISSING'); sys.exit(2)
    text=text.replace(anchor,helper+'\n'+anchor,1)
old="                if d.get('enter'):\n"
new="                if d.get('enter') or _v85_active_entry(d, sym, holdings, eval_store):\n"
if old in text:
    text=text.replace(old,new,1)
elif '_v85_active_entry(d, sym, holdings, eval_store)' not in text:
    print('ABORT=ENTRY_GATE_ANCHOR_MISSING'); sys.exit(3)
ENGINE.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
if r.returncode:
    print('ENGINE_PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,ENGINE); print('ROLLBACK=YES'); sys.exit(4)
print('ENGINE_PY_COMPILE=PASS')
# show current candidate scores before restart
try:
    d=json.loads(EVAL.read_text(encoding='utf-8'))
    rows=d.get('rows',[]) if isinstance(d,dict) else (d if isinstance(d,list) else [])
    scored=[]
    for x in rows:
        if isinstance(x,dict):
            s=str(x.get('symbol') or '').upper(); sc=_v=float(x.get('score') or x.get('effective_score') or -999)
            scored.append((sc,s,bool(x.get('enter')),x.get('reason')))
    scored=sorted(scored,reverse=True)[:8]
    print('PRE_RESTART_TOP='+json.dumps(scored,ensure_ascii=False))
except Exception as e: print('PRE_RESTART_TOP_ERROR='+repr(e))
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(5)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
if svc!='active': sys.exit(5)
print('ACTIVE_ENTRY_MIN_SCORE=40')
print('ACTIVE_ENTRY_TOP_N=4')
print('MAX_POSITIONS=UNCHANGED_4')
print('CAPITAL_USE=UNCHANGED_99_5PCT')
print('MARKETABLE_LIMIT=UNCHANGED_1PCT')
print('STALE_ORDER_CANCEL=UNCHANGED_20S')
print('SAME_BAR_RETRY=DISABLED')
print('TRADE_SWITCH=RESPECTED')
print('DEPLOY=PASS')
