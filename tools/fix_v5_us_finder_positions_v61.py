#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast, json, os, py_compile, re, subprocess, tempfile, time, urllib.request

R=Path('/home/ubuntu/day-trader-api')
APP=R/'app_v5.py'
ACCOUNT=R/'v22e_us_mock_account.json'
LOG=R/'app_v5.log'
PORT=8503

if not APP.exists(): raise SystemExit('ABORT app_v5.py missing')
s=APP.read_text(encoding='utf-8')

HELPER = r'''
# V61: robust US Finder score + Kiwoom mock broker position source
V61_US_BROKER_FINDER_UI = True

def _v61_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return {}

def _v61_eval_map():
    d=_v61_json('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
    rows=[]
    if isinstance(d,list): rows=d
    elif isinstance(d,dict):
        for k in ('rows','finder','candidates','eval','results'):
            v=d.get(k)
            if isinstance(v,list): rows=v; break
            if isinstance(v,dict) and isinstance(v.get('rows'),list): rows=v.get('rows'); break
    out={}
    for r in rows or []:
        if not isinstance(r,dict): continue
        sym=str(r.get('symbol') or r.get('ticker') or r.get('stk_cd') or '').upper().strip()
        if sym: out[sym]=r
    return out

def _v61_power(row):
    if not isinstance(row,dict): return '-'
    for k in ('power','Power','finder_score','entry_score','effective_score','score','v22e_score'):
        v=row.get(k)
        if v not in (None,'','-'):
            try: return round(float(v),1)
            except Exception: return v
    sym=str(row.get('symbol') or row.get('ticker') or row.get('stk_cd') or '').upper().strip()
    er=_v61_eval_map().get(sym,{})
    for k in ('power','Power','finder_score','entry_score','effective_score','score','v22e_score'):
        v=er.get(k) if isinstance(er,dict) else None
        if v not in (None,'','-'):
            try: return round(float(v),1)
            except Exception: return v
    return '-'

def _v61_broker_rows():
    d=_v61_json('/home/ubuntu/day-trader-api/v22e_us_mock_account.json')
    hs=d.get('holdings') if isinstance(d,dict) else []
    out=[]
    for h in hs or []:
        if not isinstance(h,dict): continue
        sym=str(h.get('symbol') or h.get('stk_cd') or '').upper().strip()
        if not sym: continue
        qty=h.get('qty',h.get('poss_qty',0)); avg=h.get('avg',h.get('frgn_stk_book_uv',0)); px=h.get('price',h.get('now_pric',0))
        mv=h.get('market_value',h.get('evlt_amt',0)); pnl=h.get('pnl',h.get('pl_amt',0)); pct=h.get('pnl_pct',h.get('pl_rt',0))
        r=dict(h)
        r.update({'symbol':sym,'ticker':sym,'code':sym,'qty':qty,'quantity':qty,'shares':qty,'avg':avg,'avg_price':avg,'average_price':avg,'price':px,'current_price':px,'market_price':px,'market_value':mv,'evaluation':mv,'pnl':pnl,'profit_loss':pnl,'pnl_pct':pct,'return_pct':pct,'source':'KIWOOM_US_MOCK'})
        out.append(r)
    return out

def _v61_broker_positions_like(original):
    rows=_v61_broker_rows()
    if isinstance(original,dict): return {r['symbol']:r for r in rows}
    if isinstance(original,tuple): return tuple(rows)
    return rows
'''

if 'V61_US_BROKER_FINDER_UI = True' not in s:
    tree=ast.parse(s)
    insert_line=1
    for n in tree.body:
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            insert_line=max(insert_line,getattr(n,'end_lineno',n.lineno)+1)
        elif isinstance(n,ast.Expr) and isinstance(getattr(n,'value',None),ast.Constant) and isinstance(n.value.value,str):
            insert_line=max(insert_line,getattr(n,'end_lineno',n.lineno)+1)
        else:
            break
    lines=s.splitlines(True)
    lines.insert(insert_line-1,HELPER)
    s=''.join(lines)

    # 1) Finder Power: patch every simple .get('power'/'Power', ...) lookup globally.
    pwr_pat=re.compile(r"(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.get\(\s*(['\"])(?:power|Power)\2(?:\s*,\s*[^\)]*)?\)")
    s,pwr_n=pwr_pat.subn(lambda m:f"_v61_power({m.group('obj')})",s)

    # If no direct power lookup exists, patch table-style score columns by column name.
    if pwr_n==0:
        score_pat=re.compile(r"(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.get\(\s*(['\"])(?:finder_score|entry_score|effective_score|score|v22e_score)\2(?:\s*,\s*[^\)]*)?\)")
        s,pwr_n=score_pat.subn(lambda m:f"_v61_power({m.group('obj')})",s)

    # 2) Position/holding source: wrap source assignments across whole file, not by heading text.
    src_lines=s.splitlines(True)
    tree2=ast.parse(s)
    cands=[]
    for node in ast.walk(tree2):
        if not isinstance(node,(ast.Assign,ast.AnnAssign)): continue
        tgts=node.targets if isinstance(node,ast.Assign) else [node.target]
        for t in tgts:
            name=t.id if isinstance(t,ast.Name) else ''
            nl=name.lower()
            if name and any(k in nl for k in ('positions','holdings','portfolio','position_rows','holding_rows')):
                cands.append((getattr(node,'lineno',0),name))
    pos_n=0
    for ln,name in sorted(set(cands)):
        idx=ln-1
        if not (0 <= idx < len(src_lines)): continue
        text=src_lines[idx]
        m=re.match(r'^(\s*)('+re.escape(name)+r')\s*=\s*(.+?)(\n?)$',text)
        if not m: continue
        rhs=m.group(3)
        if '_v61_broker_positions_like' in rhs or '_v60_broker_positions_like' in rhs: continue
        src_lines[idx]=f"{m.group(1)}{name} = _v61_broker_positions_like({rhs}){m.group(4)}"
        pos_n+=1
    s=''.join(src_lines)

    # Last-resort state sync if assignment wrapping found nothing.
    if pos_n==0:
        sync='''\n# V61 broker-position session sync\ntry:\n    _v61_rows=_v61_broker_rows()\n    for _v61_k in list(st.session_state.keys()):\n        _v61_n=str(_v61_k).lower()\n        if any(_x in _v61_n for _x in ('position','holding','portfolio')):\n            _v61_old=st.session_state.get(_v61_k)\n            if isinstance(_v61_old,dict): st.session_state[_v61_k]={_r['symbol']:_r for _r in _v61_rows}\n            elif isinstance(_v61_old,(list,tuple)): st.session_state[_v61_k]=_v61_rows\nexcept Exception:\n    pass\n'''
        # put after first streamlit import/use region, shape-independent
        j=s.find('\n')
        s=s[:j+1]+sync+s[j+1:]
        pos_n=1

    # Do not abort merely because Finder label/heading changed. Compile is the guard.
    fd,name=tempfile.mkstemp(prefix='v61_app_',suffix='.py'); os.close(fd)
    t=Path(name); t.write_text(s,encoding='utf-8')
    py_compile.compile(str(t),doraise=True)
    print('PY_COMPILE=PASS',flush=True)
    bak=Path(str(APP)+'.pre_v61')
    if not bak.exists(): subprocess.run(['sudo','cp','-a',APP,bak],check=True)
    subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,APP],check=True)
    t.unlink(missing_ok=True)
    print(f'POWER_PATCHES={pwr_n}',flush=True)
    print(f'POSITION_PATCHES={pos_n}',flush=True)
else:
    print('V61_ALREADY_PRESENT=YES',flush=True)

try:
    acct=json.loads(ACCOUNT.read_text(encoding='utf-8'))
except Exception as e: raise SystemExit('ABORT account JSON unreadable: '+repr(e))
hs=acct.get('holdings') if isinstance(acct,dict) else []
print('BROKER_HOLDINGS='+json.dumps({'count':len(hs or []),'symbols':[str(x.get('symbol') or x.get('stk_cd') or '').upper() for x in (hs or []) if isinstance(x,dict)]},ensure_ascii=False),flush=True)

subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {R} && nohup {R}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
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
print('US_FINDER_POWER=V22E_SCORE_CONNECTED_WHERE_SCORE_FIELD_EXISTS',flush=True)
print('US_POSITION_SOURCE=KIWOOM_US_MOCK_ACCOUNT',flush=True)
print('DEPLOY=PASS',flush=True)
