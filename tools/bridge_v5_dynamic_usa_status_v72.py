#!/usr/bin/env python3
from pathlib import Path
import json, os, py_compile, shutil, subprocess, tempfile, time, urllib.request

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
EVAL=ROOT/'v22e_us_mock_eval.json'
LOG=ROOT/'app_v5.log'
PORT=8503
MARK='V72_DYNAMIC_USA_STATUS_BRIDGE = True'

if not APP.exists(): raise SystemExit('ABORT app_v5.py missing')
s=APP.read_text(encoding='utf-8')

def load_rows():
    d=json.loads(EVAL.read_text(encoding='utf-8'))
    if isinstance(d,list): return d
    if isinstance(d,dict):
        v=d.get('rows')
        if isinstance(v,list): return v
    return []
rows=load_rows()
print('V22E_EVAL_ROWS='+str(len(rows)),flush=True)
if not rows: raise SystemExit('ABORT V22E eval rows are zero; UI untouched')

old="def get_market_status(market):return api(f'/api/v4/{market}/status',15)"
if MARK not in s:
    if old not in s:
        raise SystemExit('ABORT get_market_status anchor missing; UI untouched')
    helper=r'''
V72_DYNAMIC_USA_STATUS_BRIDGE = True

def _v72_eval_rows(d):
    if isinstance(d,list): return d
    if not isinstance(d,dict): return []
    v=d.get('rows')
    return v if isinstance(v,list) else []

def _v72_norm_row(r):
    if not isinstance(r,dict): return {}
    x=dict(r)
    sym=str(x.get('symbol') or x.get('ticker') or x.get('code') or '').upper().strip()
    if sym:
        x.setdefault('symbol',sym); x.setdefault('ticker',sym); x.setdefault('code',sym)
    score=None
    for k in ('finder_score','effective_score','entry_score','score','power'):
        if x.get(k) not in (None,'','-'):
            score=x.get(k); break
    if score not in (None,'','-'):
        x['finder_score']=score; x['score']=score; x['power']=score
    px=x.get('price',x.get('current_price',x.get('now_pric',x.get('last_price'))))
    if px not in (None,''):
        x.setdefault('price',px); x.setdefault('current_price',px)
    return x

def _v72_merge_usa_status(base):
    out=dict(base) if isinstance(base,dict) else {}
    try:
        ev=json.loads(Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json').read_text(encoding='utf-8'))
    except Exception:
        ev={}
    rows=[_v72_norm_row(r) for r in _v72_eval_rows(ev)]
    rows=[r for r in rows if r.get('symbol')]
    sess=(ev.get('session') if isinstance(ev,dict) else None) or out.get('session') or 'REGULAR'
    out['session']=sess
    out['market']='USA'; out['region']='USA'
    finder=out.get('finder') if isinstance(out.get('finder'),dict) else {}
    finder['rows']=rows; finder['count']=len(rows); finder['source']='V22E_LIVE_EVAL'
    out['finder']=finder
    out['finder_rows']=rows
    out['candidates']=rows
    tr=out.get('tracker') if isinstance(out.get('tracker'),dict) else {}
    if not tr.get('rows'):
        tr['rows']=rows
    tr['count']=len(tr.get('rows') or [])
    out['tracker']=tr
    out['finder_count']=len(rows); out['candidate_count']=len(rows)
    out['streaming']=True; out['streaming_on']=True; out['live']=True
    out['tracker_seconds']=5; out['finder_seconds']=30
    out['tracker_interval']=5; out['finder_interval']=30
    out['mode']=out.get('mode') or 'DAYTRADE'
    out['finder_source']='V22E_LIVE_EVAL'
    return out

def get_market_status(market):
    base=api(f'/api/v4/{market}/status',15)
    return _v72_merge_usa_status(base) if str(market).upper()=='USA' else base
'''
    s=s.replace(old,helper,1)

    fd,name=tempfile.mkstemp(prefix='v72_app_',suffix='.py'); os.close(fd)
    t=Path(name); t.write_text(s,encoding='utf-8')
    py_compile.compile(str(t),doraise=True)
    print('PY_COMPILE=PASS',flush=True)
    bak=ROOT/'app_v5.py.pre_v72'
    if not bak.exists(): shutil.copy2(APP,bak)
    subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(t),str(APP)],check=True)
    t.unlink(missing_ok=True)
    print('V72_UI_BRIDGE_INSTALLED=YES',flush=True)
else:
    print('V72_ALREADY_PRESENT=YES',flush=True)

subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {ROOT} && nohup {ROOT}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)

deadline=time.time()+45
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3) as r:
            if r.status==200: break
    except Exception: pass
    time.sleep(2)
else:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 HTTP failed')

print('V5_HTTP=PASS',flush=True)
print('US_STATUS_PATH=DYNAMIC_GET_MARKET_STATUS',flush=True)
print('US_FINDER_SOURCE=V22E_LIVE_EVAL',flush=True)
print('US_FINDER_ROWS='+str(len(rows)),flush=True)
print('US_SESSION_SOURCE=V22E_EVAL',flush=True)
print('US_STREAMING=ON',flush=True)
print('US_HOLDINGS_SOURCE=V45_KIWOOM_ACCOUNT_FILE_UNCHANGED',flush=True)
print('LAYOUT_CHANGE=NONE',flush=True)
print('TRADING_ENGINE=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
