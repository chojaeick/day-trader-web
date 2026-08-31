#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import shutil
import subprocess
import time
import urllib.request

RUNTIME = Path('/home/ubuntu/day-trader-api')
API = RUNTIME / 'live_server' / 'api.py'
BACKUP = API.with_suffix('.py.pre_v22_kr_mock_hotfix')

OLD_ENABLED = """# ===== DAYTRADE ENTRY AUTO V1.3 =====\n_daytrade_entry_auto_status={\n    'enabled':True,\n"""
NEW_ENABLED = """# ===== DAYTRADE ENTRY AUTO V1.3 =====\n# V22 KR MOCK HOTFIX: this legacy signal-only runner calls the removed\n# KoreaMarketAdapter.daytrade_entry_v12().  Keep it disabled; the real\n# Williams mock order bridge runs independently in CleanEngine.\n_daytrade_entry_auto_status={\n    'enabled':False,\n"""

OLD_ACCOUNT = """                if b.cfg.order_enable:\n                    bal=await asyncio.to_thread(\n                        b.request_account,\n                        'kt00004',\n                        {'qry_tp':'0','dmst_stex_tp':'KRX'}\n                    )\n                    now=_time.monotonic()\n"""
NEW_ACCOUNT = """                if b.cfg.order_enable:\n                    # V22 KR MOCK HOTFIX: kt00004 is read-only and Kiwoom mock\n                    # occasionally returns transient 502. Retry only this account\n                    # read; never auto-retry buy/sell requests because that could\n                    # duplicate an order.\n                    bal=None\n                    account_error=None\n                    for account_attempt in range(3):\n                        try:\n                            bal=await asyncio.to_thread(\n                                b.request_account,\n                                'kt00004',\n                                {'qry_tp':'0','dmst_stex_tp':'KRX'}\n                            )\n                            account_error=None\n                            break\n                        except Exception as e:\n                            account_error=e\n                            if account_attempt >= 2:\n                                raise\n                            logging.warning(\n                                'V123 mock account retry attempt=%s error=%s',\n                                account_attempt+1, e\n                            )\n                            await asyncio.sleep(0.75*(account_attempt+1))\n                    if bal is None:\n                        raise RuntimeError(f'V123 mock account unavailable: {account_error}')\n                    now=_time.monotonic()\n"""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True)


def main() -> None:
    if not API.exists():
        raise SystemExit(f'API not found: {API}')
    text = API.read_text(encoding='utf-8')

    if OLD_ENABLED in text:
        text = text.replace(OLD_ENABLED, NEW_ENABLED, 1)
    elif "'enabled':False" not in text or 'V22 KR MOCK HOTFIX' not in text:
        raise SystemExit('legacy daytrade runner block not found; refusing blind patch')

    if OLD_ACCOUNT in text:
        text = text.replace(OLD_ACCOUNT, NEW_ACCOUNT, 1)
    elif 'V123 mock account retry attempt=' not in text:
        raise SystemExit('V123 account block not found; refusing blind patch')

    if not BACKUP.exists():
        shutil.copy2(API, BACKUP)
    API.write_text(text, encoding='utf-8')
    py_compile.compile(str(API), doraise=True)
    print('PATCH=PASS')
    print(f'BACKUP={BACKUP}')

    run('sudo', 'systemctl', 'restart', 'day-trader-api')

    deadline = time.time() + 45
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as r:
                last = r.read().decode('utf-8', 'replace')
                if r.status == 200:
                    print('HEALTH=PASS')
                    print(last)
                    break
        except Exception as e:
            last = repr(e)
        time.sleep(1)
    else:
        print(f'HEALTH=FAIL last={last}')
        raise SystemExit(2)

    print('WAITING_FOR_POST_RESTART_LOOPS=12s')
    time.sleep(12)
    run(
        'sudo', 'journalctl', '-u', 'day-trader-api', '--since', '-90 seconds', '--no-pager',
        check=False,
    )


if __name__ == '__main__':
    main()
