from __future__ import annotations

from pathlib import Path
import py_compile
import shutil
import subprocess
import time
import urllib.request

REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNTIME=Path('/home/ubuntu/day-trader-api')
SERVICE='day-trader-api'


def run(*args):
    print('+',' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True)


def main():
    src=REPO/'live_server/kr_live_console.py'
    dst=RUNTIME/'live_server/kr_live_console.py'
    if not src.exists():raise SystemExit(f'ABORT missing {src}')
    shutil.copy2(src,dst)
    py_compile.compile(str(dst),doraise=True)
    print('INSTALLED live_server/kr_live_console.py',flush=True)

    api=RUNTIME/'live_server/api.py'
    text=api.read_text()
    backup=api.with_name('api.py.pre_kr_live_console')
    if not backup.exists():shutil.copy2(api,backup)
    marker='# V22_KR_LIVE_CONSOLE_REGISTERED'
    if marker not in text:
        needle="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)"
        if needle not in text:
            raise SystemExit('ABORT FastAPI app anchor not found')
        insert=needle+"\n\n# V22_KR_LIVE_CONSOLE_REGISTERED\nfrom .kr_live_console import register_kr_live_console\nregister_kr_live_console(app, s.db_path)"
        text=text.replace(needle,insert,1)
        api.write_text(text)
        print('API_ROUTE=PATCHED',flush=True)
    else:
        print('API_ROUTE=ALREADY_PATCHED',flush=True)
    py_compile.compile(str(api),doraise=True)

    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+45
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                if r.status==200:
                    print('HEALTH=PASS',flush=True);break
        except Exception:pass
        time.sleep(2)
    else:raise SystemExit('ABORT service health timeout')

    with urllib.request.urlopen('http://127.0.0.1:8000/api/korea-live/state',timeout=20) as r:
        body=r.read().decode('utf-8','replace')
        print('STATE_API_STATUS=',r.status,flush=True)
        print(body[:1200],flush=True)
    with urllib.request.urlopen('http://127.0.0.1:8000/korea-live',timeout=10) as r:
        print('DASHBOARD_STATUS=',r.status,flush=True)
    print('KR_LIVE_CONSOLE=DEPLOYED',flush=True)
    print('URL_PATH=/korea-live',flush=True)

if __name__=='__main__':main()
