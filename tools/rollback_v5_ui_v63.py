#!/usr/bin/env python3
from pathlib import Path
import os, py_compile, shutil, subprocess, tempfile, time, urllib.request

R=Path('/home/ubuntu/day-trader-api')
APP=R/'app_v5.py'
LOG=R/'app_v5.log'
PORT=8503

# Find real runtime backups. Exclude any backup that already contains V60/V61 markers.
cands=[]
for p in R.glob('app_v5.py.pre_*'):
    try:
        txt=p.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        continue
    if 'V60_US_BROKER_FINDER_UI = True' in txt or 'V61_' in txt:
        continue
    try:
        py_compile.compile(str(p), doraise=True)
    except Exception:
        continue
    cands.append((p.stat().st_mtime, p))

if not cands:
    # Also consider generic backups created by prior installers.
    for pat in ('app_v5.py.bak*','app_v5.py.backup*','app_v5.py.pre_v5*','app_v5.py.pre_v4*'):
        for p in R.glob(pat):
            try:
                txt=p.read_text(encoding='utf-8', errors='ignore')
                if 'V60_US_BROKER_FINDER_UI = True' in txt or 'V61_' in txt:
                    continue
                py_compile.compile(str(p), doraise=True)
                cands.append((p.stat().st_mtime,p))
            except Exception:
                pass

if not cands:
    raise SystemExit('ABORT no usable pre-V61 app_v5 backup found')

cands.sort(reverse=True)
src=cands[0][1]
print('RESTORE_SOURCE='+str(src), flush=True)

# Save broken current file for forensic inspection, then restore chosen backup.
broken=R/'app_v5.py.broken_v61'
if APP.exists() and not broken.exists():
    subprocess.run(['sudo','cp','-a',APP,broken],check=True)

fd,tmpname=tempfile.mkstemp(prefix='v63_app_',suffix='.py'); os.close(fd)
tmp=Path(tmpname)
shutil.copy2(src,tmp)
py_compile.compile(str(tmp),doraise=True)
print('PY_COMPILE=PASS',flush=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',tmp,APP],check=True)
tmp.unlink(missing_ok=True)

# Restart Streamlit only. Trading engine/API untouched.
subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {R} && nohup {R}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)

deadline=time.time()+45; last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3) as r:
            if r.status==200:
                break
    except Exception as e:
        last=e
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
print('V61_UI_PATCH=REMOVED',flush=True)
print('TRADING_ENGINE=UNTOUCHED',flush=True)
print('DEPLOY=PASS',flush=True)
