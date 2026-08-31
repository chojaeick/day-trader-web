#!/usr/bin/env python3
from pathlib import Path
import subprocess, shutil, time, sys
ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
PY=ROOT/'venv/bin/python'
print('V83_START=YES')
text=APP.read_text(encoding='utf-8')
bak=ROOT/f'app_v5.py.pre_v83_{int(time.time())}'
shutil.copy2(APP,bak)
# V82 helper uses Path at runtime. Add only the missing import, preserving all V82 wiring.
if 'from pathlib import Path' not in text:
    lines=text.splitlines(True)
    insert_at=0
    # keep shebang / module docstring / future imports safe; ordinary import at top is enough for this app
    if lines and lines[0].startswith('#!'):
        insert_at=1
    lines.insert(insert_at,'from pathlib import Path\n')
    text=''.join(lines)
    APP.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
if r.returncode:
    print('PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,APP); print('ROLLBACK=YES'); sys.exit(2)
print('PY_COMPILE=PASS')
# restart V5 only; engine remains untouched
subprocess.run("pkill -f 'streamlit run .*app_v5.py' || true",shell=True)
time.sleep(1)
subprocess.run("cd /home/ubuntu/day-trader-api && nohup /home/ubuntu/day-trader-api/venv/bin/streamlit run app_v5.py --server.port 8503 --server.address 0.0.0.0 > /home/ubuntu/day-trader-api/app_v5.log 2>&1 &",shell=True)
time.sleep(4)
try:
    import urllib.request
    body=urllib.request.urlopen('http://127.0.0.1:8503/',timeout=5).read(128)
    print('V5_HTTP=PASS')
except Exception as e:
    print('V5_HTTP=FAIL',repr(e)); sys.exit(3)
print('FIX=MISSING_PATH_IMPORT_ONLY')
print('ENGINE_RESTART=NO')
print('KR_PATH=UNCHANGED')
print('DEPLOY=PASS')
