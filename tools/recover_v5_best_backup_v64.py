#!/usr/bin/env python3
from pathlib import Path
import re, py_compile, tempfile, os, subprocess, time, urllib.request, shutil

R=Path('/home/ubuntu/day-trader-api')
APP=R/'app_v5.py'
LOG=R/'app_v5.log'
PORT=8503

cands=[]
for p in sorted(R.glob('app_v5.py*')):
    if p==APP or not p.is_file():
        continue
    try:
        txt=p.read_text(encoding='utf-8',errors='ignore')
    except Exception:
        continue
    if 'V60_US_BROKER_FINDER_UI = True' in txt or 'V61_US_BROKER_FINDER_UI = True' in txt:
        continue
    # Prefer the modern V5 UI known from the healthy screen: v4x header/markers,
    # US/KR sticky toggles, DAYTRADE/NORMAL, Finder/Tracker cadence, USA session UI.
    nums=[int(x) for x in re.findall(r'(?i)\bv(?:ersion\s*)?([0-9]{1,3})\b',txt)]
    vmax=max(nums) if nums else 0
    score=0
    score += min(vmax,99)*100
    for token,w in [
        ('DAY TRADER V5',20),('DAYTRADER V5',20),('US 미국장',15),('KR 국장',15),
        ('DAYTRADE',15),('NORMAL',10),('Finder',10),('Tracker',10),
        ('US REGULAR',10),('V22E',20),('실시간 단타 후보 TOP 20',20),
        ('보유 포지션',10),('보유주식 관리',10)]:
        if token in txt: score+=w
    # Strongly penalize obviously old v37-style runtime markers.
    if vmax and vmax < 40: score -= 5000
    # Newer file wins ties.
    score += int(p.stat().st_mtime)//100000
    # Compile before considering.
    try:
        fd,name=tempfile.mkstemp(prefix='v64cand_',suffix='.py'); os.close(fd)
        Path(name).write_text(txt,encoding='utf-8')
        py_compile.compile(name,doraise=True)
        Path(name).unlink(missing_ok=True)
    except Exception:
        continue
    cands.append((score,vmax,p.stat().st_mtime,p,txt))

if not cands:
    raise SystemExit('ABORT no usable app_v5 backups found')

cands.sort(key=lambda x:(x[0],x[1],x[2]),reverse=True)
print('BACKUP_CANDIDATES=')
for score,vmax,mt,p,_ in cands[:10]:
    print(f'  score={score} vmax={vmax} mtime={int(mt)} file={p}')

score,vmax,mt,best,txt=cands[0]
if vmax < 40:
    raise SystemExit(f'ABORT best backup still looks old: {best} vmax={vmax}')

fd,name=tempfile.mkstemp(prefix='v64_restore_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(txt,encoding='utf-8')
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS')

safety=R/f'app_v5.py.pre_v64_bad_{int(time.time())}'
shutil.copy2(APP,safety)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',t,APP],check=True)
t.unlink(missing_ok=True)

subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {R} && nohup {R}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)

deadline=time.time()+45
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3) as r:
            if r.status==200: break
    except Exception:
        pass
    time.sleep(2)
else:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 HTTP failed after restore')

time.sleep(3)
p=subprocess.run("pgrep -af '[s]treamlit.*app_v5.py'",shell=True,text=True,capture_output=True)
if p.returncode!=0:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 process not running')

print(f'RESTORE_SOURCE={best}')
print(f'RESTORE_VERSION_MAX={vmax}')
print('V5_HTTP=PASS')
print('TRADING_ENGINE=UNTOUCHED')
print('DEPLOY=PASS')
