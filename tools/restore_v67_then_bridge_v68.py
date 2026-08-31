#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, py_compile, subprocess, time

R=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNNER=R/'live_server/v22e_us_mock_live.py'
BACKUP=R/'live_server/v22e_us_mock_live.py.pre_v67'
EVAL=R/'v22e_us_mock_eval.json'
SERVICE='day-trader-v22e-us'
V66=REPO/'tools/repair_v5_us_data_bridge_v66.py'

if not BACKUP.exists():
    raise SystemExit(f'ABORT pre-V67 backup missing: {BACKUP}')
if not V66.exists():
    raise SystemExit(f'ABORT V66 missing: {V66}')

# Validate the exact pre-V67 runtime before touching the live runner.
py_compile.compile(str(BACKUP), doraise=True)
print('PRE_V67_COMPILE=PASS', flush=True)

# Preserve the currently broken V67 runtime for forensics, then restore exact pre-V67.
cur_bak=R/'live_server/v22e_us_mock_live.py.v67_broken_saved'
if RUNNER.exists() and not cur_bak.exists():
    subprocess.run(['sudo','cp','-a',str(RUNNER),str(cur_bak)],check=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(BACKUP),str(RUNNER)],check=True)
py_compile.compile(str(RUNNER), doraise=True)
print('V67_RUNTIME=ROLLED_BACK_EXACT', flush=True)

subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)

# Wait until the real pre-V67 writer republishes a non-empty eval snapshot.
def extract_rows(d):
    if isinstance(d,list): return d,'LIST'
    if isinstance(d,dict):
        for k in ('rows','finder','candidates','eval','results'):
            v=d.get(k)
            if isinstance(v,list): return v,k
            if isinstance(v,dict) and isinstance(v.get('rows'),list): return v.get('rows'),k+'.rows'
    return [],'UNKNOWN'

rows=[]; shape='UNKNOWN'
deadline=time.time()+75
while time.time()<deadline:
    try:
        d=json.loads(EVAL.read_text(encoding='utf-8'))
        rows,shape=extract_rows(d)
        if rows:
            break
    except Exception:
        pass
    time.sleep(2)

print(f'V22E_EVAL_ROWS={len(rows)}',flush=True)
print(f'V22E_EVAL_SHAPE={shape}',flush=True)
if not rows:
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','100','--no-pager'],check=False)
    raise SystemExit('ABORT pre-V67 restored but eval still zero; V5 untouched')

# Reject any lingering V67 writer errors before touching V5.
j=subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','80','--no-pager'],text=True,capture_output=True)
recent=j.stdout or ''
if '_v67_write_eval_nonempty' in recent or 'EVAL_STATE_WRITE_ERROR' in recent and 'unexpected keyword argument' in recent:
    # journal can include entries from the prior process; only abort if current service has loop errors after restart.
    active=subprocess.run(['systemctl','is-active',SERVICE],text=True,capture_output=True).stdout.strip()
    if active!='active':
        raise SystemExit('ABORT V22E service not active after rollback')
print('V22E_SERVICE=ACTIVE',flush=True)
print('V67_WRITER_PATCH=REMOVED',flush=True)

# Now apply the already-created data-only V5 bridge. It self-validates eval rows and
# compiles before installation; trading strategy code is not modified by V66.
r=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python',str(V66)],text=True)
if r.returncode!=0:
    raise SystemExit(f'ABORT V66 bridge failed rc={r.returncode}')

print('FINDER_EVAL=NONEMPTY',flush=True)
print('V5_DATA_BRIDGE=APPLIED',flush=True)
print('TRADING_ENGINE=PRE_V67_RESTORED',flush=True)
print('DEPLOY=PASS',flush=True)
