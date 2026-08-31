#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import os, re, subprocess, tempfile, py_compile, time, urllib.request, hashlib

R=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
APP=R/'app_v5.py'
PORT=8503
LOG=R/'app_v5.log'

# Markers visible in the known-good V47-style US terminal immediately before V61.
MARKERS=[
    ('DAYTRADER V5',5),
    ('총자산',5),
    ('투자금(원금)',5),
    ('현금',4),
    ('오늘 손익',4),
    ('실시간 단타 후보 TOP 20',10),
    ('선택 종목 상세',10),
    ('보유 포지션',7),
    ('V22E',8),
    ('Finder TOP 20',4),
    ('Streaming ALWAYS_ON',4),
    ('DAYTRADE',3),
]
BAD=[('MODE UNKNOWN',-20),('런타임 모드 확인 실패',-20),('최근 단타 후보 TOP 5',-15),('V60_US_BROKER_FINDER_UI',-20)]

def score_text(s:str):
    score=0; hits=[]
    for m,w in MARKERS:
        if m in s: score+=w; hits.append(m)
    for m,w in BAD:
        if m in s: score+=w
    # Favor explicit UI generation markers in the v40s, not patch script version numbers.
    versions=[]
    for pat in (r'DAYTRADER\s*V5[^\n]{0,80}?v(\d+)', r'UI_VERSION\s*=\s*[\"\']?v?(\d+)', r'V5_UI_VERSION\s*=\s*[\"\']?v?(\d+)'):
        versions += [int(x) for x in re.findall(pat,s,re.I)]
    vmax=max(versions) if versions else 0
    if 40 <= vmax <= 59: score += 25
    elif vmax >= 60: score -= 5
    return score,hits,vmax

def readable_py(p:Path):
    try:
        if not p.is_file() or p.stat().st_size<5000 or p.stat().st_size>5_000_000: return None
        s=p.read_text(encoding='utf-8',errors='ignore')
        if 'streamlit' not in s.lower() or 'DAYTRADER' not in s.upper(): return None
        compile(s,str(p),'exec')
        return s
    except Exception:
        return None

cands=[]; seen=set()
def add_candidate(label:str,s:str,mtime:float=0,path:str=''):
    h=hashlib.sha256(s.encode()).hexdigest()
    if h in seen: return
    seen.add(h)
    sc,hits,vmax=score_text(s)
    cands.append({'score':sc,'hits':hits,'vmax':vmax,'mtime':mtime,'label':label,'path':path,'text':s,'sha':h})

# 1) Filesystem forensic scan: runtime, repo, and /tmp, not just app_v5.py.pre_*.
roots=[R,REPO,Path('/tmp')]
patterns=['app_v5.py*','*app*v5*.py*','*v5*ui*.py*','*streamlit*.py*']
for root in roots:
    if not root.exists(): continue
    paths=[]
    for pat in patterns:
        try: paths += list(root.rglob(pat))
        except Exception: pass
    for p in paths:
        try:
            s=readable_py(p)
            if s: add_candidate('FILE',s,p.stat().st_mtime,str(p))
        except Exception: pass

# 2) Git history forensic scan for every historical app_v5.py blob on all refs.
if REPO.exists():
    try:
        out=subprocess.check_output(['git','-C',str(REPO),'log','--all','--format=%H','--','app_v5.py'],text=True,stderr=subprocess.DEVNULL)
        for sha in out.splitlines()[:250]:
            try:
                s=subprocess.check_output(['git','-C',str(REPO),'show',f'{sha}:app_v5.py'],text=True,stderr=subprocess.DEVNULL)
                if len(s)>5000 and 'streamlit' in s.lower(): add_candidate(f'GIT:{sha[:12]}',s,0,f'{sha}:app_v5.py')
            except Exception: pass
    except Exception: pass

cands.sort(key=lambda x:(x['score'],x['vmax'],x['mtime']),reverse=True)
print('FORENSIC_CANDIDATES=')
for c in cands[:12]:
    print(f"  score={c['score']} vmax={c['vmax']} hits={len(c['hits'])} mtime={int(c['mtime'])} source={c['label']} path={c['path']}")

if not cands: raise SystemExit('ABORT no V5 candidates found')
best=cands[0]
required={'총자산','현금','실시간 단타 후보 TOP 20','선택 종목 상세'}
if best['score'] < 45 or not required.issubset(set(best['hits'])):
    raise SystemExit('ABORT no candidate matches known-good V47-style UI strongly enough; NOTHING_INSTALLED')

# Never reinstall the currently degraded content just because it is newest.
try:
    current=APP.read_text(encoding='utf-8',errors='ignore')
    if hashlib.sha256(current.encode()).hexdigest()==best['sha'] and ('MODE UNKNOWN' in current or '최근 단타 후보 TOP 5' in current):
        raise SystemExit('ABORT best candidate is current degraded UI; NOTHING_INSTALLED')
except FileNotFoundError: pass

fd,tmpname=tempfile.mkstemp(prefix='v65_v5_',suffix='.py'); os.close(fd)
tmp=Path(tmpname); tmp.write_text(best['text'],encoding='utf-8')
py_compile.compile(str(tmp),doraise=True)
print('PY_COMPILE=PASS')
print(f"RESTORE_SOURCE={best['label']} {best['path']}")
print(f"RESTORE_SCORE={best['score']}")
print('RESTORE_MARKERS='+','.join(best['hits']))

# Preserve current broken UI for audit, then install only the vetted candidate.
if APP.exists():
    subprocess.run(['sudo','cp','-a',str(APP),str(APP)+'.broken_before_v65'],check=True)
subprocess.run(['sudo','install','-o','ubuntu','-g','ubuntu','-m','0644',str(tmp),str(APP)],check=True)
tmp.unlink(missing_ok=True)

subprocess.run("sudo pkill -f '[s]treamlit.*app_v5.py' || true",shell=True,check=False)
time.sleep(2)
cmd=f"cd {R} && nohup {R}/venv/bin/streamlit run {APP} --server.address 0.0.0.0 --server.port {PORT} > {LOG} 2>&1 &"
subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=True)
last=None
deadline=time.time()+45
while time.time()<deadline:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/',timeout=3) as r:
            if r.status==200: break
    except Exception as e: last=e
    time.sleep(2)
else:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 HTTP failed after install: '+repr(last))
time.sleep(3)
p=subprocess.run("pgrep -af '[s]treamlit.*app_v5.py'",shell=True,text=True,capture_output=True)
if p.returncode!=0:
    subprocess.run(['tail','-n','120',str(LOG)],check=False)
    raise SystemExit('ABORT V5 process not running')
print('V5_HTTP=PASS')
print('TRADING_ENGINE=UNTOUCHED')
print('DEPLOY=PASS')
