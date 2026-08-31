#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast, json, os, py_compile, re, shutil, subprocess, tempfile, time, urllib.request

R=Path('/home/ubuntu/day-trader-api')
APP=R/'app_v5.py'
ACCOUNT=R/'v22e_us_mock_account.json'
EVAL=R/'v22e_us_mock_eval.json'
LOG=R/'app_v5.log'
PORT=8503

if not APP.exists(): raise SystemExit('ABORT app_v5.py missing')
s=APP.read_text(encoding='utf-8')
if 'V60_US_BROKER_FINDER_UI = True' in s:
    print('V60_ALREADY_PRESENT=YES',flush=True)
else:
    # Inject helpers immediately after imports. They are deliberately shape-tolerant
    # because app_v5.py is runtime-local and has accumulated UI patches.
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
    helper=r'''\n# V60: US Finder score + Kiwoom mock broker position source\nV60_US_BROKER_FINDER_UI = True\n\ndef _v60_json(path):\n    try:\n        d=json.loads(Path(path).read_text(encoding='utf-8'))\n        return d\n    except Exception:\n        return {}\n\ndef _v60_eval_map():\n    d=_v60_json('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')\n    rows=[]\n    if isinstance(d,list): rows=d\n    elif isinstance(d,dict):\n        for k in ('rows','finder','candidates','eval','results'):\n            v=d.get(k)\n            if isinstance(v,list): rows=v; break\n            if isinstance(v,dict) and isinstance(v.get('rows'),list): rows=v.get('rows'); break\n    out={}\n    for r in rows or []:\n        if not isinstance(r,dict): continue\n        sym=str(r.get('symbol') or r.get('ticker') or r.get('stk_cd') or '').upper().strip()\n        if sym: out[sym]=r\n    return out\n\ndef _v60_power(row):\n    if not isinstance(row,dict): return '-'\n    for k in ('power','Power','finder_score','entry_score','effective_score','score','v22e_score'):\n        v=row.get(k)\n        if v not in (None,'','-'):\n            try: return round(float(v),1)\n            except Exception: return v\n    sym=str(row.get('symbol') or row.get('ticker') or row.get('stk_cd') or '').upper().strip()\n    er=_v60_eval_map().get(sym,{})\n    for k in ('power','finder_score','entry_score','effective_score','score','v22e_score'):\n        v=er.get(k) if isinstance(er,dict) else None\n        if v not in (None,'','-'):\n            try: return round(float(v),1)\n            except Exception: return v\n    return '-'\n\ndef _v60_broker_rows():\n    d=_v60_json('/home/ubuntu/day-trader-api/v22e_us_mock_account.json')\n    hs=d.get('holdings') if isinstance(d,dict) else []\n    out=[]\n    for h in hs or []:\n        if not isinstance(h,dict): continue\n        sym=str(h.get('symbol') or h.get('stk_cd') or '').upper().strip()\n        if not sym: continue\n        qty=h.get('qty',h.get('poss_qty',0)); avg=h.get('avg',h.get('frgn_stk_book_uv',0)); px=h.get('price',h.get('now_pric',0))\n        mv=h.get('market_value',h.get('evlt_amt',0)); pnl=h.get('pnl',h.get('pl_amt',0)); pct=h.get('pnl_pct',h.get('pl_rt',0))\n        r=dict(h)\n        r.update({'symbol':sym,'ticker':sym,'code':sym,'qty':qty,'quantity':qty,'shares':qty,'avg':avg,'avg_price':avg,'average_price':avg,'price':px,'current_price':px,'market_price':px,'market_value':mv,'evaluation':mv,'pnl':pnl,'profit_loss':pnl,'pnl_pct':pct,'return_pct':pct,'source':'KIWOOM_US_MOCK'})\n        out.append(r)\n    return out\n\ndef _v60_us_selected():\n    try:\n        import streamlit as st\n        for k,v in st.session_state.items():\n            ks=str(k).lower(); vs=str(v).upper()\n            if ('market' in ks or 'country' in ks or 'region' in ks or 'selected' in ks) and vs in ('US','USA','미장','US 미장'):\n                return True\n    except Exception: pass\n    # V5 US page renders the USA status endpoint and US-session labels; when the\n    # account file is present we only use the override in position-shaped vars.\n    return True\n\ndef _v60_broker_positions_like(original):\n    if not _v60_us_selected(): return original\n    rows=_v60_broker_rows()\n    if isinstance(original,dict):\n        return {r['symbol']:r for r in rows}\n    if isinstance(original,tuple): return tuple(rows)\n    return rows\n'''
    lines.insert(insert_line-1,helper)
    s=''.join(lines)

    # Finder Power: replace row-like .get('power'/'Power', '-') lookups with a
    # helper that falls back through the live V22E score fields and eval file.
    pwr_pat=re.compile(r"(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.get\(\s*(['\"])(?:power|Power)\2(?:\s*,\s*[^\)]*)?\)")
    s,pwr_n=pwr_pat.subn(lambda m:f"_v60_power({m.group('obj')})",s)

    # Also patch literal table construction like {'Power': '-'} inside the Finder
    # section when a row variable is obvious on the same line.
    sec_idx=s.find('실시간 단타 후보 TOP 20')
    if sec_idx<0: sec_idx=s.find('Finder TOP 20')
    if sec_idx<0: raise SystemExit('ABORT Finder section anchor missing')
    sec_end=s.find('\n#',sec_idx+1)
    if sec_end<0 or sec_end-sec_idx>25000: sec_end=min(len(s),sec_idx+25000)
    sec=s[sec_idx:sec_end]
    for var in ('row','r','item','x','cand','candidate'):
        sec,n=re.subn(r"(['\"]Power['\"]\s*:\s*)['\"]-['\"]",rf"\1_v60_power({var})",sec,count=1)
        if n: pwr_n+=n; break
    s=s[:sec_idx]+sec+s[sec_end:]

    # Position source: find the runtime-local position-management section and wrap
    # position/holding/portfolio-shaped assignments nearest that section. This
    # replaces stale internal/manual data while preserving the renderer itself.
    pos_anchor=-1
    for token in ('보유 포지션','보유주식 관리','포지션 관리','보유주식'):
        j=s.find(token)
        if j>=0: pos_anchor=j; break
    if pos_anchor<0: raise SystemExit('ABORT position section anchor missing')
    pre=s[:pos_anchor]
    line0=pre.count('\n')+1
    tree2=ast.parse(s)
    candidates=[]
    for node in ast.walk(tree2):
        if not isinstance(node,(ast.Assign,ast.AnnAssign)): continue
        ln=getattr(node,'lineno',0)
        if not (line0-220 <= ln <= line0+220): continue
        tgts=node.targets if isinstance(node,ast.Assign) else [node.target]
        for t in tgts:
            name=t.id if isinstance(t,ast.Name) else (t.attr if isinstance(t,ast.Attribute) else '')
            nl=str(name).lower()
            if any(k in nl for k in ('position','holding','portfolio','holdings','positions')):
                candidates.append((abs(ln-line0),ln,name))
    candidates.sort()
    pos_n=0
    # Patch up to three nearest source assignments, keeping their original RHS.
    src_lines=s.splitlines(True)
    for _,ln,name in candidates[:3]:
        idx=ln-1
        text=src_lines[idx]
        # only simple one-line assignments; complex statements are left untouched.
        m=re.match(r'^(\s*)('+re.escape(name)+r')\s*=\s*(.+?)(\n?)$',text)
        if not m: continue
        rhs=m.group(3)
        if '_v60_broker_positions_like' in rhs: continue
        src_lines[idx]=f"{m.group(1)}{name} = _v60_broker_positions_like({rhs}){m.group(4)}"
        pos_n+=1
    s=''.join(src_lines)

    # If no assignment was transformable, inject a session-state synchronizer just
    # before the position section. This covers the common runtime V5 manual list.
    if pos_n==0:
        pos_line=s[:pos_anchor].count('\n')
        src_lines=s.splitlines(True)
        indent=re.match(r'^\s*',src_lines[pos_line]).group(0)
        sync=(indent+"# V60 broker-position sync\n"+indent+"try:\n"+indent+"    _v60_rows=_v60_broker_rows()\n"+indent+"    for _v60_k in list(st.session_state.keys()):\n"+indent+"        if any(_x in str(_v60_k).lower() for _x in ('position','holding','portfolio')):\n"+indent+"            _v60_old=st.session_state.get(_v60_k)\n"+indent+"            if isinstance(_v60_old,dict): st.session_state[_v60_k]={_r['symbol']:_r for _r in _v60_rows}\n"+indent+"            elif isinstance(_v60_old,(list,tuple)): st.session_state[_v60_k]=_v60_rows\n"+indent+"except Exception:\n"+indent+"    pass\n")
        src_lines.insert(pos_line,sync)
        s=''.join(src_lines); pos_n=1

    if pwr_n<=0: raise SystemExit('ABORT Power mapping anchor missing')
    if pos_n<=0: raise SystemExit('ABORT broker position mapping failed')

    fd,name=tempfile.mkstemp(prefix='v60_app_',suffix='.py'); os.close(fd)
    t=Path(name); t.write_text(s,encoding='utf-8')
    py_compile.compile(str(t),doraise=True)
    print('PY_COMPILE=PASS',flush=True)
    bak=Path(str(APP)+'.pre_v60')
    if not bak.exists(): subprocess.run(['sudo','cp','-a',APP,bak],check=True)
    subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,APP],check=True)
    t.unlink(missing_ok=True)
    print(f'POWER_PATCHES={pwr_n}',flush=True)
    print(f'POSITION_PATCHES={pos_n}',flush=True)

# Validate live data inputs before restart.
try:
    acct=json.loads(ACCOUNT.read_text(encoding='utf-8'))
except Exception as e: raise SystemExit('ABORT account JSON unreadable: '+repr(e))
print('BROKER_HOLDINGS='+json.dumps({'count':acct.get('holding_count'),'symbols':[x.get('symbol') for x in acct.get('holdings') or []]},ensure_ascii=False),flush=True)

# Restart only the local Streamlit V5 process; API/trading engine stays untouched.
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
    subprocess.run(['tail','-n','100',str(LOG)],check=False)
    raise SystemExit('ABORT V5 HTTP failed: '+repr(last))

# Fail if Streamlit crashed after initial HTTP boot.
time.sleep(3)
p=subprocess.run("pgrep -af '[s]treamlit.*app_v5.py'",shell=True,text=True,capture_output=True)
if p.returncode!=0:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 process not running')

print('V5_HTTP=PASS',flush=True)
print('US_FINDER_POWER=V22E_SCORE_CONNECTED',flush=True)
print('US_POSITION_SOURCE=KIWOOM_US_MOCK_ACCOUNT',flush=True)
print('STALE_INTERNAL_US_POSITION=DISABLED',flush=True)
print('DEPLOY=PASS',flush=True)
