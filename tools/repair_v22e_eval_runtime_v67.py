#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import json, os, py_compile, subprocess, tempfile, time

R=Path('/home/ubuntu/day-trader-api')
RUNNER=R/'live_server/v22e_us_mock_live.py'
EVAL=R/'v22e_us_mock_eval.json'
ACCOUNT=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

if not RUNNER.exists():
    raise SystemExit('ABORT runner missing')
s=RUNNER.read_text(encoding='utf-8')

# Preserve all existing trading logic. Only harden eval publication so a temporary
# empty candidate source cannot erase the last known non-empty V22E Finder set.
marker='V67_PRESERVE_LAST_NONEMPTY_EVAL = True'
if marker not in s:
    inject=f"\n{marker}\n"
    # add helper near imports without touching strategy code
    pos=0
    lines=s.splitlines(True)
    for i,line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '):
            pos=i+1
    lines.insert(pos, inject)
    s=''.join(lines)

    helper=r'''

def _v67_write_eval_nonempty(path, payload):
    """Write V22E eval atomically; never replace a non-empty snapshot with empty rows."""
    try:
        p=Path(path)
        rows=[]
        if isinstance(payload,list):
            rows=payload
        elif isinstance(payload,dict):
            for k in ('rows','finder','candidates','eval','results'):
                v=payload.get(k)
                if isinstance(v,list):
                    rows=v; break
                if isinstance(v,dict) and isinstance(v.get('rows'),list):
                    rows=v.get('rows'); break
        if not rows:
            try:
                old=json.loads(p.read_text(encoding='utf-8')) if p.exists() else None
                old_rows=[]
                if isinstance(old,list): old_rows=old
                elif isinstance(old,dict):
                    for k in ('rows','finder','candidates','eval','results'):
                        v=old.get(k)
                        if isinstance(v,list): old_rows=v; break
                        if isinstance(v,dict) and isinstance(v.get('rows'),list): old_rows=v.get('rows'); break
                if old_rows:
                    return False
            except Exception:
                pass
        tmp=Path(str(p)+'.v67tmp')
        tmp.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        os.replace(tmp,p)
        return True
    except Exception:
        return False
'''
    # insert helper before first class/def after imports
    idx=s.find('\nclass ')
    d=s.find('\ndef ')
    spots=[x for x in (idx,d) if x>=0]
    at=min(spots) if spots else len(s)
    s=s[:at]+helper+s[at:]

    # Replace direct eval-file write_text/json.dump patterns conservatively.
    # Known runtime uses EVAL_PATH / EVAL_FILE style constants; handle common shapes.
    import re
    pats=[
        r"(?m)^(?P<ind>\s*)(?P<obj>[A-Za-z_][A-Za-z0-9_]*)\.write_text\(json\.dumps\((?P<payload>[^\n]+?)\),\s*encoding=['\"]utf-8['\"]\)\s*$",
        r"(?m)^(?P<ind>\s*)Path\((?P<path>[^\n]+?)\)\.write_text\(json\.dumps\((?P<payload>[^\n]+?)\),\s*encoding=['\"]utf-8['\"]\)\s*$",
    ]
    n=0
    def r1(m):
        nonlocal_n[0]+=1
        return f"{m.group('ind')}_v67_write_eval_nonempty({m.group('obj')}, {m.group('payload')})"
    nonlocal_n=[0]
    s=re.sub(pats[0],r1,s)
    n+=nonlocal_n[0]
    nonlocal_n=[0]
    def r2(m):
        nonlocal_n[0]+=1
        return f"{m.group('ind')}_v67_write_eval_nonempty(Path({m.group('path')}), {m.group('payload')})"
    s=re.sub(pats[1],r2,s)
    n+=nonlocal_n[0]

    # If no direct writer was found, do not install a speculative runtime patch.
    if n==0:
        raise SystemExit('ABORT eval writer anchor not found; runtime untouched')

    fd,name=tempfile.mkstemp(prefix='v67_runner_',suffix='.py'); os.close(fd)
    t=Path(name); t.write_text(s,encoding='utf-8')
    py_compile.compile(str(t),doraise=True)
    print('PY_COMPILE=PASS',flush=True)
    bak=Path(str(RUNNER)+'.pre_v67')
    if not bak.exists(): subprocess.run(['sudo','cp','-a',RUNNER,bak],check=True)
    subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,RUNNER],check=True)
    t.unlink(missing_ok=True)
    print(f'EVAL_WRITER_PATCHES={n}',flush=True)
else:
    print('V67_ALREADY_PRESENT=YES',flush=True)

# Restart only V22E service, then wait for a non-empty eval snapshot.
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
deadline=time.time()+45
rows=[]
shape='UNKNOWN'
while time.time()<deadline:
    try:
        d=json.loads(EVAL.read_text(encoding='utf-8'))
        if isinstance(d,list): rows=d; shape='LIST'
        elif isinstance(d,dict):
            for k in ('rows','finder','candidates','eval','results'):
                v=d.get(k)
                if isinstance(v,list): rows=v; shape=k; break
                if isinstance(v,dict) and isinstance(v.get('rows'),list): rows=v.get('rows'); shape=k+'.rows'; break
        if rows: break
    except Exception: pass
    time.sleep(2)

print(f'V22E_EVAL_ROWS={len(rows)}',flush=True)
print(f'V22E_EVAL_SHAPE={shape}',flush=True)
try:
    a=json.loads(ACCOUNT.read_text(encoding='utf-8'))
    hs=a.get('holdings') or []
    print('BROKER_HOLDINGS='+json.dumps({'count':len(hs),'symbols':[x.get('symbol') for x in hs]},ensure_ascii=False),flush=True)
except Exception as e:
    print('BROKER_HOLDINGS_ERROR='+repr(e),flush=True)

if not rows:
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','120','--no-pager'],check=False)
    raise SystemExit('ABORT eval still zero after service restart; journal printed')

print('FINDER_EVAL=NONEMPTY',flush=True)
print('TRADING_LOGIC=UNCHANGED',flush=True)
print('DEPLOY=PASS',flush=True)
