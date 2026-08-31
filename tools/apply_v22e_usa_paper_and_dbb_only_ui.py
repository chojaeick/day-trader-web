#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os, py_compile, re, subprocess, tempfile, time, urllib.request

REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNTIME=Path('/home/ubuntu/day-trader-api')
APP=REPO/'app_v5.py'
API=RUNTIME/'live_server/api.py'
MOD_SRC=REPO/'live_server/engine5_v22e_usa.py'
MOD_DST=RUNTIME/'live_server/engine5_v22e_usa.py'
SERVICE='day-trader-api'; PORT=8503; LOG='/tmp/daytrader-v5.log'

def run(*a):
    print('+',' '.join(map(str,a)),flush=True); subprocess.run(list(map(str,a)),check=True)

def install_text(dst:Path,text:str):
    fd,tmp=tempfile.mkstemp(prefix='v22e_patch_',suffix='.py');os.close(fd);p=Path(tmp)
    try:
        p.write_text(text,encoding='utf-8');py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,dst)
    finally:p.unlink(missing_ok=True)

def patch_app():
    s=APP.read_text(encoding='utf-8')
    new=r'''def engine_matrix(row):
    """Trading detail exposes only the active DBB engine."""
    row=row or {}
    market=str(row.get('market') or '').upper()
    v22e=row.get('engine5_v22e_decision') or {}
    v22=row.get('engine5_v22_decision') or {}
    d=v22e or v22
    score=d.get('effective_score') if d else None
    if score is None and d: score=d.get('score')
    if score is None: score=row.get('finder_score')
    action=action_ko(action_of(row))
    risk=str(row.get('risk') or 'NORMAL')
    name='DBB V22E' if market=='USA' else 'DBB V22'
    state='LIVE' if d else '대기'
    return [{'엔진':name,'상태':state,'점수':'-' if score is None else f'{f(score):.1f}','판단':action,'위험':risk}]
'''
    pat=re.compile(r'^def engine_matrix\(.*?(?=^def |^@st\.|\Z)',re.M|re.S)
    m=pat.search(s)
    if not m: raise SystemExit('ABORT engine_matrix function missing')
    s=s[:m.start()]+new+'\n'+s[m.end():]
    if "name='DBB V22E' if market=='USA' else 'DBB V22'" not in new:
        raise SystemExit('ABORT DBB-only engine label missing')
    APP.write_text(s,encoding='utf-8');py_compile.compile(str(APP),doraise=True)
    print('TRADING_ENGINE_MATRIX=DBB_ONLY',flush=True)

def patch_api():
    s=API.read_text(encoding='utf-8')
    marker='# V22E_USA_PAPER_RUNTIME'
    if marker not in s:
        anchor='async def korea_discovery_forever():\n'
        if anchor not in s: raise SystemExit('ABORT api helper anchor missing')
        helper=r'''# V22E_USA_PAPER_RUNTIME
_v22e_usa_executor=None
_v22e_usa_last={'engine':'ENGINE5_V22E_USA_PAPER','status':'INIT','updated_at':None,'actions':[]}

def _get_v22e_usa_executor():
    global _v22e_usa_executor
    if _v22e_usa_executor is None:
        from live_server.engine5_v22e_usa import V22EUsaExecutor
        _v22e_usa_executor=V22EUsaExecutor(s.db_path)
    return _v22e_usa_executor

async def v22e_usa_paper_forever():
    global _v22e_usa_last
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _et=_ZI('America/New_York')
    while True:
        try:
            now=_dt.now(_et); mins=now.hour*60+now.minute
            regular=bool(now.weekday()<5 and 570<=mins<960)
            enabled=bool(_runtime_profile().get('mode')=='DAYTRADE' and regular)
            if enabled:
                ex=_get_v22e_usa_executor()
                res=await asyncio.to_thread(ex.step,v4,db)
                res['status']='RUNNING';res['updated_at']=datetime.now(timezone.utc).isoformat();_v22e_usa_last=res
                for a in (res.get('actions') or []):
                    if a and a.get('ok'):
                        logging.warning('V22E_USA_PAPER_ACTION side=%s sym=%s qty=%s fill=%s reason=%s engine=%s',a.get('side'),a.get('symbol'),a.get('qty'),a.get('fill_price'),a.get('reason'),a.get('engine'))
            else:
                _v22e_usa_last={**_v22e_usa_last,'status':'WAITING_DAYTRADE_REGULAR','updated_at':datetime.now(timezone.utc).isoformat()}
        except Exception:
            logging.exception('V22E USA paper loop failed')
            _v22e_usa_last={**_v22e_usa_last,'status':'ERROR','updated_at':datetime.now(timezone.utc).isoformat()}
        await asyncio.sleep(5)

@app.get('/api/v22e/usa/state')
def v22e_usa_state():
    try:
        ex=_get_v22e_usa_executor(); acct=ex.broker.account()
        return {'ok':True,**_v22e_usa_last,'account':acct,'authority':'DBB_V22E_ONLY','broker':'INTERNAL_USA_PAPER_ONLY'}
    except Exception as e:
        return {'ok':False,'error':str(e),'authority':'DBB_V22E_ONLY','broker':'INTERNAL_USA_PAPER_ONLY'}

@app.get('/api/v22e/usa/trades')
def v22e_usa_trades(limit:int=100):
    limit=max(1,min(int(limit),1000))
    import sqlite3 as _sqlite3
    c=_sqlite3.connect(s.db_path);c.row_factory=_sqlite3.Row
    try: rows=[dict(r) for r in c.execute('SELECT * FROM v22e_usa_trades ORDER BY id DESC LIMIT ?',(limit,)).fetchall()]
    finally:c.close()
    return {'ok':True,'engine':'ENGINE5_V22E_USA_PAPER','rows':rows}

'''
        s=s.replace(anchor,helper+anchor,1)
    task='asyncio.create_task(v22e_usa_paper_forever())'
    if task not in s:
        needle='asyncio.create_task(v4_engine_forever())'
        p=s.find(needle)
        if p<0: raise SystemExit('ABORT v4_engine task anchor missing')
        # IMPORTANT: keep existing list syntax valid; insert a complete list element line.
        line_start=s.rfind('\n',0,p)+1
        indent=re.match(r'[ \t]*',s[line_start:p]).group(0)
        s=s[:line_start]+indent+task+',\n'+s[line_start:]
    if s.count(task)!=1: raise SystemExit('ABORT V22E task schedule count='+str(s.count(task)))
    install_text(API,s)
    print('V22E_USA_API_RUNTIME=PATCHED',flush=True)

def service_diag():
    subprocess.run(['sudo','systemctl','status',SERVICE,'--no-pager','-l'],check=False)
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','80','--no-pager'],check=False)

def main():
    if not APP.exists() or not API.exists() or not MOD_SRC.exists(): raise SystemExit('ABORT required file missing')
    py_compile.compile(str(MOD_SRC),doraise=True);run('sudo','install','-m','0644',MOD_SRC,MOD_DST)
    print('V22E_USA_MODULE=INSTALLED',flush=True)
    patch_app();patch_api()
    run(RUNTIME/'venv/bin/python','-m','py_compile',MOD_DST,API)
    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+60;last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                if r.status==200:print('API_HEALTH=PASS',flush=True);break
        except Exception as e:last=e
        time.sleep(2)
    else:
        print('API_HEALTH=FAIL',flush=True);service_diag();raise SystemExit('ABORT API health '+str(last))
    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False);time.sleep(1)
    cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
    deadline=time.time()+45;last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=2) as r:
                if r.status==200:print('V5_HTTP=PASS',flush=True);break
        except Exception as e:last=e
        time.sleep(2)
    else:raise SystemExit('ABORT V5 startup '+str(last))
    print('TRADING_DETAIL_ENGINES=DBB_ONLY',flush=True)
    print('KR_ENGINE=DBB_V22_ONLY',flush=True)
    print('USA_ENGINE=DBB_V22E_PAPER_ONLY',flush=True)
    print('USA_PAPER_LEDGER=ISOLATED_V22E_TABLES',flush=True)
    print('USA_EXECUTION_WINDOW=DAYTRADE_AND_US_REGULAR_ONLY',flush=True)
    print('USA_BROKER=INTERNAL_PAPER_NO_REAL_ORDERS',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':main()
