#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, tempfile, os, py_compile, time, urllib.request

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
SERVICE='day-trader-api'

def run(*a):
    print('+',' '.join(map(str,a)),flush=True); subprocess.run(list(map(str,a)),check=True)

def install_text(dst,text):
    fd,tmp=tempfile.mkstemp(prefix='api_restore_',suffix='.py'); os.close(fd); p=Path(tmp)
    try:
        p.write_text(text,encoding='utf-8'); py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,dst)
    finally:p.unlink(missing_ok=True)

s=API.read_text(encoding='utf-8')
# Remove the complete V22E helper/route block that was inserted before app creation.
start=s.find('# V22E_USA_PAPER_RUNTIME')
if start>=0:
    # In the bad patch this block was inserted immediately before korea_discovery_forever.
    end=s.find('async def korea_discovery_forever():',start)
    if end<0: raise SystemExit('ABORT cannot find end of V22E block')
    s=s[:start]+s[end:]
    print('BROKEN_V22E_API_BLOCK=REMOVED',flush=True)
else:
    print('BROKEN_V22E_API_BLOCK=NOT_PRESENT',flush=True)

# Remove any scheduled task reference left in lifespan.
s=re.sub(r'\s*asyncio\.create_task\(v22e_usa_paper_forever\(\)\)\s*,?\s*','\n                      ',s)
# clean accidental duplicate commas/newlines conservatively
s=s.replace('[\n                      ,','[\n                      ')
install_text(API,s)
run('sudo','systemctl','restart',SERVICE)

deadline=time.time()+60; last=None
while time.time()<deadline:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
            if r.status==200:
                print('API_HEALTH=PASS',flush=True); break
    except Exception as e:last=e
    time.sleep(2)
else:
    subprocess.run(['sudo','systemctl','status',SERVICE,'--no-pager','-l'],check=False)
    subprocess.run(['sudo','journalctl','-u',SERVICE,'-n','120','--no-pager'],check=False)
    raise SystemExit('ABORT API restore failed: '+str(last))

print('INTERNAL_V22E_PAPER_RUNTIME=DISCONNECTED',flush=True)
print('EXISTING_US_MOCK_CONNECTION=TO_BE_REUSED',flush=True)
print('DEPLOY=PASS',flush=True)
