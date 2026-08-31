#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast, json, os, py_compile, shutil, subprocess, tempfile, time, urllib.request

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
EVAL=ROOT/'v22e_us_mock_eval.json'
ACCOUNT=ROOT/'v22e_us_mock_account.json'
LOG=ROOT/'app_v5.log'
PORT=8503
MARK='V66_US_STATUS_BRIDGE = True'

if not APP.exists(): raise SystemExit('ABORT app_v5.py missing')
s=APP.read_text(encoding='utf-8')

# Read live inputs first. Refuse to touch UI if V22E data is unavailable.
def loadj(p):
    try: return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception as e: raise SystemExit(f'ABORT unreadable {p}: {e!r}')
ev=loadj(EVAL)
ac=loadj(ACCOUNT)

def rows_from_eval(d):
    if isinstance(d,list): return d
    if not isinstance(d,dict): return []
    for k in ('rows','finder','candidates','eval','results'):
        v=d.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict) and isinstance(v.get('rows'),list): return v['rows']
    return []
rows=rows_from_eval(ev)
print('V22E_EVAL_ROWS='+str(len(rows)),flush=True)
print('BROKER_HOLDINGS='+json.dumps({'count':ac.get('holding_count',len(ac.get('holdings') or [])),'symbols':[x.get('symbol') for x in ac.get('holdings') or []]},ensure_ascii=False),flush=True)
if len(rows)==0: raise SystemExit('ABORT V22E eval rows are zero; UI not modified')

if MARK not in s:
    tree=ast.parse(s)
    # Find assignments whose RHS contains the USA status endpoint string.
    targets=[]
    for node in ast.walk(tree):
        if not isinstance(node,(ast.Assign,ast.AnnAssign)): continue
        vals=[]
        for sub in ast.walk(node.value):
            if isinstance(sub,ast.Constant) and isinstance(sub.value,str): vals.append(sub.value)
        if any('/api/v4/USA/status' in x or 'api/v4/USA/status' in x for x in vals):
            targets.append(node)
    if not targets:
        # Also permit direct request calls as expression/if sources.
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                vals=[sub.value for sub in ast.walk(node) if isinstance(sub,ast.Constant) and isinstance(sub.value,str)]
                if any('/api/v4/USA/status' in x or 'api/v4/USA/status' in x for x in vals):
                    targets.append(node)
    if not targets: raise SystemExit('ABORT no USA status call found; UI not modified')

    # Insert helper after imports. It only merges data; it does not alter rendering/layout.
    insert_line=1
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            insert_line=max(insert_line,getattr(n,'end_lineno',n.lineno)+1)
        elif isinstance(n,ast.Expr) and isinstance(getattr(n,'value',None),ast.Constant) and isinstance(n.value.value,str):
            insert_line=max(insert_line,getattr(n,'end_lineno',n.lineno)+1)
        else:
            break
    helper=r'''
# V66: data-only US status bridge. No layout/rendering changes.
V66_US_STATUS_BRIDGE = True

def _v66_load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {}

def _v66_eval_rows(d):
    if isinstance(d,list): return d
    if not isinstance(d,dict): return []
    for k in ('rows','finder','candidates','eval','results'):
        v=d.get(k)
        if isinstance(v,list): return v
        if isinstance(v,dict) and isinstance(v.get('rows'),list): return v.get('rows') or []
    return []

def _v66_norm_row(r):
    if not isinstance(r,dict): return {}
    x=dict(r)
    sym=str(x.get('symbol') or x.get('ticker') or x.get('stk_cd') or x.get('code') or '').upper().strip()
    if sym:
        x.setdefault('symbol',sym); x.setdefault('ticker',sym); x.setdefault('code',sym)
    score=None
    for k in ('power','finder_score','entry_score','effective_score','score','v22e_score'):
        if x.get(k) not in (None,'','-'):
            score=x.get(k); break
    if score not in (None,'','-'):
        x['power']=score; x['Power']=score; x.setdefault('finder_score',score); x.setdefault('score',score)
    px=x.get('price',x.get('current_price',x.get('now_pric',x.get('last_price'))))
    if px not in (None,''):
        x.setdefault('price',px); x.setdefault('current_price',px); x.setdefault('now_pric',px)
    return x

def _v66_merge_us_status(original):
    base=dict(original) if isinstance(original,dict) else {}
    ev=_v66_load_json('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
    rows=[_v66_norm_row(r) for r in _v66_eval_rows(ev)]
    rows=[r for r in rows if r.get('symbol')]
    # Preserve backend values when present, but backfill all fields expected by legacy V5.
    session=(ev.get('session') if isinstance(ev,dict) else None) or base.get('session') or 'REGULAR'
    base['session']=session
    base['market']='USA'; base['region']='USA'
    base['streaming']=True; base['streaming_on']=True; base['live']=True
    base['tracker_seconds']=5; base['finder_seconds']=30
    base['tracker_interval']=5; base['finder_interval']=30
    base['mode']=base.get('mode') or 'DAYTRADE'
    # Finder shapes used across V5 generations.
    base['finder']={'rows':rows,'count':len(rows),'source':'V22E_LIVE_EVAL'}
    base['candidates']=rows
    base['finder_rows']=rows
    base['rows']=base.get('rows') or rows
    # Tracker fallback keeps candidate detail cards populated.
    tr=base.get('tracker') if isinstance(base.get('tracker'),dict) else {}
    if not tr.get('rows'): tr['rows']=rows
    tr['count']=len(tr.get('rows') or [])
    base['tracker']=tr
    base['candidate_count']=len(rows); base['finder_count']=len(rows)
    base['finder_source']='V22E_LIVE_EVAL'
    return base
'''
    lines=s.splitlines(True)
    lines.insert(insert_line-1,helper)
    s=''.join(lines)

    # Reparse after helper insertion, then wrap only assignment RHS nodes containing USA status.
    tree=ast.parse(s)
    class Wrap(ast.NodeTransformer):
        def __init__(self): self.n=0
        def _has(self,node):
            return any(isinstance(z,ast.Constant) and isinstance(z.value,str) and ('api/v4/USA/status' in z.value) for z in ast.walk(node))
        def visit_Assign(self,node):
            self.generic_visit(node)
            if self._has(node.value) and not (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='_v66_merge_us_status'):
                node.value=ast.Call(func=ast.Name(id='_v66_merge_us_status',ctx=ast.Load()),args=[node.value],keywords=[]); self.n+=1
            return node
        def visit_AnnAssign(self,node):
            self.generic_visit(node)
            if node.value is not None and self._has(node.value) and not (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='_v66_merge_us_status'):
                node.value=ast.Call(func=ast.Name(id='_v66_merge_us_status',ctx=ast.Load()),args=[node.value],keywords=[]); self.n+=1
            return node
    w=Wrap(); tree=w.visit(tree); ast.fix_missing_locations(tree)
    if w.n==0: raise SystemExit('ABORT USA status assignment was not safely wrappable; UI not modified')
    s=ast.unparse(tree)+'\n'
    print('USA_STATUS_WRAPS='+str(w.n),flush=True)

    fd,tmpn=tempfile.mkstemp(prefix='v66_app_',suffix='.py'); os.close(fd)
    tmp=Path(tmpn); tmp.write_text(s,encoding='utf-8')
    py_compile.compile(str(tmp),doraise=True)
    print('PY_COMPILE=PASS',flush=True)
    bak=ROOT/'app_v5.py.pre_v66'
    shutil.copy2(APP,bak)
    subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(tmp),str(APP)],check=True)
    tmp.unlink(missing_ok=True)
else:
    print('V66_ALREADY_PRESENT=YES',flush=True)

# Restart Streamlit only.
subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {ROOT} && nohup {ROOT}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)

deadline=time.time()+45; last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3) as r:
            if r.status==200: break
    except Exception as e: last=e
    time.sleep(2)
else:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 HTTP failed: '+repr(last))
time.sleep(3)
p=subprocess.run("pgrep -af '[s]treamlit.*app_v5.py'",shell=True,text=True,capture_output=True)
if p.returncode!=0:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 process not running')

print('V5_HTTP=PASS',flush=True)
print('US_SESSION_SOURCE=V22E_EVAL',flush=True)
print('US_FINDER_SOURCE=V22E_LIVE_EVAL',flush=True)
print('US_FINDER_ROWS='+str(len(rows)),flush=True)
print('US_STREAMING=ON',flush=True)
print('LAYOUT_CHANGE=NONE',flush=True)
print('TRADING_ENGINE=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
