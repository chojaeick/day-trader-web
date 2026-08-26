#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/ubuntu/day-trader-api')
REPO = Path('/home/ubuntu/day-trader-api-repo')
TARGET = REPO / 'tools' / 'v221_kiwoom_mock_usa_real_order_smoke.py'
SERVICE = 'day-trader-api.service'
VENV_PY = ROOT / 'venv' / 'bin' / 'python3'

ALLOWED = {
    'KIWOOM_MOCK_APP_KEY',
    'KIWOOM_MOCK_APP_SECRET',
    'KIWOOM_MOCK_REST_BASE',
    'KIWOOM_MOCK_ORDER_ENABLE',
}


def die(msg: str, code: int = 2) -> None:
    print('V221B_ABORT', msg)
    raise SystemExit(code)


def main() -> None:
    print('=== V221B RUN USA KIWOOM MOCK ORDER USING SERVICE ENV ===')
    print('SOURCE_ENV=', SERVICE)
    print('REAL_ACCOUNT_ENV_IMPORTED=NO')
    print('ALLOWED_ENV_KEYS=', sorted(ALLOWED))

    if not TARGET.exists():
        die(f'missing target {TARGET}')
    if not VENV_PY.exists():
        die(f'missing venv python {VENV_PY}')

    p = subprocess.run(
        ['systemctl', 'show', SERVICE, '--property=Environment', '--value'],
        text=True,
        capture_output=True,
    )
    if p.returncode != 0:
        print(p.stderr.strip())
        die('systemctl show failed')

    raw = p.stdout.strip()
    if not raw:
        die('service Environment is empty')

    env = os.environ.copy()
    imported = []
    for tok in shlex.split(raw):
        if '=' not in tok:
            continue
        k, v = tok.split('=', 1)
        if k in ALLOWED:
            env[k] = v
            imported.append(k)

    print('IMPORTED_KEYS=', sorted(imported))
    missing = [k for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET') if not env.get(k)]
    if missing:
        die('missing mock credentials in service env: ' + ','.join(missing))

    base = env.get('KIWOOM_MOCK_REST_BASE', 'https://mockapi.kiwoom.com').strip().rstrip('/')
    if 'mockapi.kiwoom.com' not in base:
        die('refusing non-mock base: ' + base)
    env['KIWOOM_MOCK_REST_BASE'] = base

    if env.get('KIWOOM_MOCK_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'):
        die('KIWOOM_MOCK_ORDER_ENABLE is not enabled in service env')

    print('MOCK_BASE_OK=', base)
    print('ORDER_ENABLE_OK=True')
    print('EXEC=', TARGET.name)

    rc = subprocess.call([str(VENV_PY), str(TARGET)], cwd=str(REPO), env=env)
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
