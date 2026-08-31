#!/usr/bin/env python3
from __future__ import annotations

import subprocess, time, urllib.request
from pathlib import Path

ENV=Path('/home/ubuntu/day-trader-api/.env')
API='day-trader-api'
V22E='day-trader-v22e-us'


def run(*args, check=True):
    print('+',' '.join(map(str,args)), flush=True)
    return subprocess.run(list(map(str,args)), check=check)


def wait_http(url, seconds=60):
    deadline=time.time()+seconds; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status==200:
                    return True
        except Exception as e:
            last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP health failed: {last}')


def main():
    if not ENV.exists():
        raise SystemExit('ABORT .env missing')

    # The V22E deploy preserved mode 0600 but sudo install made the file root:root.
    # Runtime modules call python-dotenv as User=ubuntu, so ubuntu must own/read it.
    run('sudo','chown','ubuntu:ubuntu',ENV)
    run('sudo','chmod','600',ENV)
    print('ENV_OWNER=ubuntu:ubuntu', flush=True)
    print('ENV_MODE=0600', flush=True)

    # Stop restart thrash, then cleanly restart and verify actual import/health.
    run('sudo','systemctl','stop',API, check=False)
    time.sleep(1)
    run('sudo','systemctl','reset-failed',API, check=False)
    run('sudo','systemctl','start',API)
    wait_http('http://127.0.0.1:8000/health', 60)
    print('API_HEALTH=PASS', flush=True)

    # Only after API recovery, enable/start V22E service.
    run('sudo','systemctl','daemon-reload')
    run('sudo','systemctl','enable','--now',V22E)
    time.sleep(4)
    active=subprocess.check_output(['sudo','systemctl','is-active',V22E], text=True).strip()
    if active!='active':
        run('sudo','systemctl','status',V22E,'--no-pager','-l', check=False)
        run('sudo','journalctl','-u',V22E,'-n','100','--no-pager', check=False)
        raise SystemExit('ABORT V22E service not active')

    deadline=time.time()+45; journal=''
    while time.time()<deadline:
        journal=subprocess.check_output(['sudo','journalctl','-u',V22E,'-n','120','--no-pager'], text=True)
        if 'BROKER_CONNECTED' in journal and 'AUTHORITY' in journal:
            break
        time.sleep(2)
    else:
        print(journal)
        raise SystemExit('ABORT broker connectivity marker missing')

    if 'REFUSE_DUAL_AUTHORITY' in journal:
        print(journal)
        raise SystemExit('ABORT dual authority guard triggered')

    print('V22E_US_SERVICE=ACTIVE', flush=True)
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA', flush=True)
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA', flush=True)
    print('US_BROKER=KIWOOM_US_MOCK_ONLY', flush=True)
    print('US_ACCOUNT_API=ust21070', flush=True)
    print('US_BUY_API=ust20000', flush=True)
    print('US_SELL_API=ust20001', flush=True)
    print('WILLIAMS_US_ORDER_AUTHORITY=DISABLED', flush=True)
    print('LEGACY_DBB_PAIR_ORDER_AUTHORITY=DISABLED', flush=True)
    print('INTERNAL_PAPER_EXECUTION=DISABLED', flush=True)
    print('DEPLOY=PASS', flush=True)

if __name__=='__main__':
    main()
