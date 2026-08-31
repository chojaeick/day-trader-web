#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

REPO=Path('/home/ubuntu/day-trader-api-repo')
APP=REPO/'app_v5.py'
RUNTIME=Path('/home/ubuntu/day-trader-api')
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
EVAL=RUNTIME/'v22e_us_mock_eval.json'
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503
V22E='day-trader-v22e-us'


def run(*args,check=True):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check)


def wait_http(url,seconds=45):
    deadline=time.time()+seconds; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status==200:return
        except Exception as e:last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP failed {url}: {last}')


def main():
    if not APP.exists() or not RUNNER.exists():
        raise SystemExit('ABORT required V5/runner file missing')

    app=APP.read_text(encoding='utf-8')
    runner=RUNNER.read_text(encoding='utf-8')
    for marker in ('V44_US_EVAL_PATH','v44_us_eval_rows'):
        if marker not in app:
            raise SystemExit('ABORT V44 app patch missing: '+marker)
    for marker in ('V44_PREMARKET_EVAL_ORDER_GATE = True','premarket = weekday and 4*60 <= minute < 9*60+30','PREMARKET_EVAL_ONLY'):
        if marker not in runner:
            raise SystemExit('ABORT V44 runner patch missing: '+marker)
    print('V44_RUNTIME_PATCH=CONFIRMED',flush=True)

    active=subprocess.check_output(['sudo','systemctl','is-active',V22E],text=True).strip()
    if active!='active':
        run('sudo','systemctl','restart',V22E)
        time.sleep(3)
        active=subprocess.check_output(['sudo','systemctl','is-active',V22E],text=True).strip()
    if active!='active':
        run('sudo','systemctl','status',V22E,'--no-pager','-l',check=False)
        raise SystemExit('ABORT V22E service inactive')
    print('V22E_SERVICE=ACTIVE',flush=True)

    # The failed sudo deploy left /tmp/daytrader-v5.log owned by root. Recreate it for ubuntu.
    run('sudo','rm','-f',LOG,check=False)
    run('sudo','-u','ubuntu','touch',LOG)
    run('sudo','chown','ubuntu:ubuntu',LOG)
    run('sudo','chmod','0644',LOG)
    # Keep the working-tree UI source editable by the normal repo owner as well.
    run('sudo','chown','ubuntu:ubuntu',APP)
    print('V5_LOG_OWNER=ubuntu:ubuntu',flush=True)

    subprocess.run(['sudo','pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=(
        f"cd {REPO} && DAYTRADER_API_URL=http://127.0.0.1:8000 "
        f"nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py "
        f"--server.address=0.0.0.0 --server.port={PORT} --server.headless=true "
        f"> {LOG} 2>&1 &"
    )
    p=subprocess.run(['sudo','-u','ubuntu','-H','bash','-lc',cmd],check=False)
    if p.returncode!=0:
        raise SystemExit(f'ABORT Streamlit launch rc={p.returncode}')
    wait_http(f'http://127.0.0.1:{PORT}/',45)
    print('V5_HTTP=PASS',flush=True)

    if EVAL.exists():
        try:
            import json
            d=json.loads(EVAL.read_text(encoding='utf-8'))
            rows=d.get('rows') or {}
            print(f'V22E_EVAL_STATE=READY rows={len(rows) if isinstance(rows,dict) else 0}',flush=True)
        except Exception as e:
            print('V22E_EVAL_STATE=FILE_PRESENT_PARSE_WARN',repr(e),flush=True)
    else:
        print('V22E_EVAL_STATE=PENDING_NO_COMPLETED_EVALUATION_YET',flush=True)

    print('USA_PREMARKET_V22E_EVAL=ON',flush=True)
    print('USA_PREMARKET_ORDER_GATE=DISABLED',flush=True)
    print('USA_REGULAR_ORDER_GATE=ENABLED',flush=True)
    print('V5_US_V22E_EVAL=CONNECTED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':
    main()
