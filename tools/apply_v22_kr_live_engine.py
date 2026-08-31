from __future__ import annotations

"""Deploy the frozen KR Engine5 V22 entry-timing policy into the live runtime.

This script is intentionally fail-closed. It refuses to claim success unless the
runtime contains the V22 policy modules and the Korea adapter can import the
live gate. It does NOT fabricate or replace the validated Engine5 source
pipeline; it only installs the frozen V22 timing policy layer and disables the
legacy missing daytrade_entry_v12 runner.
"""

from pathlib import Path
import py_compile
import shutil
import subprocess
import time
import urllib.request

REPO = Path('/home/ubuntu/day-trader-api-repo')
RUNTIME = Path('/home/ubuntu/day-trader-api')
SERVICE = 'day-trader-api'

FILES = [
    'live_server/engine5_v22_entry_policy.py',
    'live_server/engine5_v22_kr.py',
    'live_server/engine5_v22_live_kr.py',
]


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def main():
    for rel in FILES:
        src = REPO / rel
        dst = RUNTIME / rel
        if not src.exists():
            raise SystemExit(f'ABORT missing source: {src}')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        py_compile.compile(str(dst), doraise=True)
        print('INSTALLED', rel, flush=True)

    # Import test against runtime tree.
    code = (
        "from live_server.engine5_v22_kr import VERSION,MARKET; "
        "from live_server.engine5_v22_live_kr import KR_V22_LIVE_ENTRY_GATE; "
        "assert VERSION=='V22' and MARKET=='KR'; "
        "print('KR_ENGINE=ENGINE5_V22', VERSION, MARKET)"
    )
    run(RUNTIME / 'venv/bin/python', '-c', code)

    run('sudo', 'systemctl', 'restart', SERVICE)
    deadline = time.time() + 45
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as r:
                last = r.read().decode('utf-8', 'replace')
                if r.status == 200:
                    print('HEALTH=PASS', flush=True)
                    print(last, flush=True)
                    break
        except Exception as e:
            last = repr(e)
        time.sleep(2)
    else:
        raise SystemExit(f'ABORT health failed: {last}')

    print('V22_KR_POLICY_RUNTIME=INSTALLED', flush=True)
    print('IMPORTANT=entry timing policy installed; execution path wiring must be verified before claiming V22 orders', flush=True)


if __name__ == '__main__':
    main()
