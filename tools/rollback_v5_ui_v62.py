#!/usr/bin/env python3
from pathlib import Path
import py_compile, subprocess, tempfile, os, time, urllib.request, shutil

R=Path('/home/ubuntu/day-trader-api')
APP=R/'app_v5.py'
BAK=R/'app_v5.py.pre_v60'
LOG=R/'app_v5.log'
PORT=8503

if not BAK.exists():
    raise SystemExit(f'ABORT backup missing: {BAK}')

fd,name=tempfile.mkstemp(prefix='v62_restore_',suffix='.py'); os.close(fd)
t=Path(name)
shutil.copy2(BAK,t)
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS', flush=True)

subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(t),str(APP)],check=True)
t.unlink(missing_ok=True)
print('RESTORE_SOURCE=app_v5.py.pre_v60', flush=True)

subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {R} && nohup {R}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)

deadline=time.time()+45
last=None
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

print('V5_HTTP=PASS', flush=True)
print('V61_UI_PATCH=ROLLED_BACK', flush=True)
print('TRADING_ENGINE=UNTOUCHED', flush=True)
print('DEPLOY=PASS', flush=True)
