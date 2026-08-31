#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import ast, json, os, py_compile, re, subprocess, tempfile, time

R=Path('/home/ubuntu/day-trader-api')
RUNNER=R/'live_server/v22e_us_mock_live.py'
EVAL=R/'v22e_us_mock_eval.json'
SERVICE='day-trader-v22e-us'
BRIDGE=Path('/home/ubuntu/day-trader-api-repo/tools/repair_v5_us_data_bridge_v66.py')

if not RUNNER.exists():
    raise SystemExit('ABORT runner missing')

# Start from exact pre-V67 runtime whenever available, so no broken V67 wrapper survives.
PRE67=Path(str(RUNNER)+'.pre_v67')
if PRE67.exists():
    src=PRE67.read_text(encoding='utf-8')
else:
    src=RUNNER.read_text(encoding='utf-8')

if 'V67_PRESERVE_LAST_NONEMPTY_EVAL' in src or '_v67_write_eval_nonempty' in src:
    raise SystemExit('ABORT source still contains V67 patch')

marker='V69_HEARTBEAT_LIVE_EVAL_PUBLISH = True'
if marker not in src:
    tree=ast.parse(src)
    lines=src.splitlines(True)
    hb_stmt=None
    rows_expr=None

    # Find the statement that emits V22E_HEARTBEAT and infer the actual rows variable
    # from eval_rows=len(<name>) or eval_rows=<name>.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        seg=ast.get_source_segment(src,node) or ''
        if 'V22E_HEARTBEAT' not in seg:
            continue
        # locate nearest enclosing statement by line range
        for st in ast.walk(tree):
            if isinstance(st, ast.stmt) and getattr(st,'lineno',10**9) <= node.lineno <= getattr(st,'end_lineno',-1):
                if hb_stmt is None or (getattr(st,'end_lineno',0)-st.lineno) < (getattr(hb_stmt,'end_lineno',0)-hb_stmt.lineno):
                    hb_stmt=st
        for kw in node.keywords:
            if kw.arg!='eval_rows':
                continue
            v=kw.value
            if isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=='len' and v.args:
                rows_expr=ast.get_source_segment(src,v.args[0])
            else:
                rows_expr=ast.get_source_segment(src,v)
        if hb_stmt is not None and rows_expr:
            break

    if hb_stmt is None or not rows_expr:
        raise SystemExit('ABORT could not infer heartbeat eval rows expression; runtime untouched')

    ln=hb_stmt.lineno-1
    indent=re.match(r'^\s*',lines[ln]).group(0)
    block=(
        indent+"# V69: publish the exact live eval rows already used by heartbeat\n"+
        indent+"try:\n"+
        indent+f"    _v69_rows = {rows_expr}\n"+
        indent+"    if isinstance(_v69_rows, (list, tuple)) and len(_v69_rows) > 0:\n"+
        indent+"        import json as _v69_json, os as _v69_os\n"+
        indent+"        from pathlib import Path as _V69Path\n"+
        indent+"        _v69_p=_V69Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')\n"+
        indent+"        _v69_t=_V69Path(str(_v69_p)+'.v69tmp')\n"+
        indent+"        _v69_t.write_text(_v69_json.dumps({'rows':list(_v69_rows),'source':'V22E_HEARTBEAT_LIVE','session':locals().get('session') or locals().get('sess')}, ensure_ascii=False), encoding='utf-8')\n"+
        indent+"        _v69_os.replace(_v69_t,_v69_p)\n"+
        indent+"except Exception as _v69_e:\n"+
        indent+"    pass\n"
    )
    # marker near top-level imports
    insert=0
    for i,line in enumerate(lines):
        if line.startswith('import ') or line.startswith('from '): insert=i+1
    lines.insert(insert, f"\n{marker}\n")
    # line number moved by one insertion if insert <= ln
    if insert <= ln: ln += 1
    lines.insert(ln, block)
    patched=''.join(lines)
else:
    patched=src

fd,name=tempfile.mkstemp(prefix='v69_runner_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(patched,encoding='utf-8')
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)

# Preserve the pre-V69 current runtime for emergency rollback.
bak=Path(str(RUNNER)+'.pre_v69')
if not bak.exists(): subprocess.run(['sudo','cp','-a',RUNNER,bak],check=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,RUNNER],check=True)
t.unlink(missing_ok=True)
print('V67_PATCH=REMOVED',flush=True)
print('V69_LIVE_EVAL_PUBLISH=INSTALLED',flush=True)

subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)

# Wait long enough for the first completed live heartbeat cycle; restart starts at 0 by design.
deadline=time.time()+90
rows=[]
shape='UNKNOWN'
while time.time()<deadline:
    try:
        d=json.loads(EVAL.read_text(encoding='utf-8'))
        if isinstance(d,list): rows=d; shape='LIST'
        elif isinstance(d,dict):
            v=d.get('rows')
            if isinstance(v,list): rows=v; shape='rows'
        if rows: break
    except Exception:
        pass
    time.sleep(2)

print(f'V22E_EVAL_ROWS={len(rows)}',flush=True)
print(f'V22E_EVAL_SHAPE={shape}',flush=True)
if not rows:
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','120','--no-pager'],check=False)
    raise SystemExit('ABORT live heartbeat rows were not published; V5 untouched')

# Apply the already-built data-only V5 bridge now that a real non-empty source exists.
if not BRIDGE.exists():
    raise SystemExit('ABORT V66 bridge script missing')
p=subprocess.run(['sudo',str(R/'venv/bin/python'),str(BRIDGE)],text=True,capture_output=True)
print(p.stdout,end='')
if p.stderr: print(p.stderr,end='')
if p.returncode!=0:
    raise SystemExit(f'ABORT V66 bridge failed rc={p.returncode}')

print('FINDER_SOURCE=V22E_HEARTBEAT_LIVE',flush=True)
print('TRADING_LOGIC=UNCHANGED',flush=True)
print('DEPLOY=PASS',flush=True)
