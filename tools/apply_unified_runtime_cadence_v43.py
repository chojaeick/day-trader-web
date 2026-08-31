#!/usr/bin/env python3
from __future__ import annotations

import os
import py_compile
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

RUNTIME = Path('/home/ubuntu/day-trader-api')
API = RUNTIME / 'live_server' / 'api.py'
BACKUP = RUNTIME / 'live_server' / 'api.py.pre_v43_unified_cadence'
SERVICE = 'day-trader-api'
PYTHON = RUNTIME / 'venv' / 'bin' / 'python'


def run(*args, check=True, capture=False, env=None):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(
        list(map(str, args)),
        check=check,
        text=True,
        capture_output=capture,
        env=env,
    )


def wait_http(url: str, seconds: int = 60):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except Exception as e:
            last = e
        time.sleep(2)
    raise SystemExit(f'ABORT health failed: {last}')


def main():
    if not API.exists():
        raise SystemExit('ABORT runtime api.py missing')

    s = API.read_text(encoding='utf-8')
    original = s

    # 1) Unified market cadence.
    # NORMAL    : Tracker 60s / Finder 120s
    # DAYTRADE  : Tracker 5s  / Finder 30s
    # korea_tracker_seconds remains as a compatibility field but mirrors tracker_seconds.
    pat = re.compile(
        r"def _runtime_profile\(\):\n"
        r"\s+daytrade=runtime_mode\.get\('mode'\)=='DAYTRADE'\n"
        r"\s+return \{\n"
        r"\s+'mode':'DAYTRADE' if daytrade else 'NORMAL',\n"
        r"\s+'tracker_seconds':[^\n]+\n"
        r"\s+'finder_seconds':[^\n]+\n"
        r"\s+'korea_tracker_seconds':[^\n]+\n"
        r"\s+'loop_seconds':[^\n]+\n"
        r"\s+'streaming':'ALWAYS_ON',\n"
        r"\s+\}"
    )
    repl = """def _runtime_profile():
    daytrade=runtime_mode.get('mode')=='DAYTRADE'
    tracker_seconds=5 if daytrade else 60
    finder_seconds=30 if daytrade else 120
    return {
        'mode':'DAYTRADE' if daytrade else 'NORMAL',
        'tracker_seconds':tracker_seconds,
        'finder_seconds':finder_seconds,
        'korea_tracker_seconds':tracker_seconds,
        'loop_seconds':2 if daytrade else 5,
        'streaming':'ALWAYS_ON',
    }"""
    s, n_profile = pat.subn(repl, s, count=1)
    if n_profile != 1:
        raise SystemExit(f'ABORT runtime profile patch count={n_profile}')

    # 2) Remove the KR-only 5-minute Finder floor so KR follows the same profile as US.
    old = "if now-last_finder>=max(300,profile['finder_seconds']):"
    new = "if now-last_finder>=profile['finder_seconds']:"
    n_floor = s.count(old)
    if n_floor < 1:
        raise SystemExit('ABORT KR finder 300s floor anchor missing')
    s = s.replace(old, new)

    # Safety guards: no accidental order-authority changes.
    forbidden_changes = [
        ('ENGINE5_V22_KR_LIVE', original.count('ENGINE5_V22_KR_LIVE'), s.count('ENGINE5_V22_KR_LIVE')),
        ('ENGINE5_V22E_USA', original.count('ENGINE5_V22E_USA'), s.count('ENGINE5_V22E_USA')),
    ]
    for name, before, after in forbidden_changes:
        if before != after:
            raise SystemExit(f'ABORT authority token changed {name}: {before}->{after}')

    # Verify exact cadence markers in patched text.
    required = [
        "tracker_seconds=5 if daytrade else 60",
        "finder_seconds=30 if daytrade else 120",
        "'korea_tracker_seconds':tracker_seconds",
        "if now-last_finder>=profile['finder_seconds']:",
    ]
    for marker in required:
        if marker not in s:
            raise SystemExit('ABORT missing marker: ' + marker)

    # Compile to a temp file first.
    fd, tmp_name = tempfile.mkstemp(prefix='api_v43_', suffix='.py')
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.write_text(s, encoding='utf-8')
    try:
        py_compile.compile(str(tmp), doraise=True)
        print('PY_COMPILE=PASS', flush=True)

        # Preserve a one-time rollback copy, then install atomically.
        if not BACKUP.exists():
            run('sudo', 'cp', '-a', API, BACKUP)
        run('sudo', 'install', '-m', '0644', tmp, API)

        # Import-test the actual installed runtime BEFORE restarting systemd.
        env = os.environ.copy()
        env['PYTHONPATH'] = str(RUNTIME)
        p = run(
            'sudo', '-u', 'ubuntu', '-H',
            PYTHON, '-c',
            "import live_server.api; p=live_server.api._runtime_profile(); print('IMPORT_PROFILE',p)",
            capture=True,
            env=env,
        )
        print(p.stdout.strip(), flush=True)
        if 'IMPORT_PROFILE' not in p.stdout:
            raise RuntimeError('runtime import profile marker missing')

    except Exception:
        if BACKUP.exists():
            run('sudo', 'install', '-m', '0644', BACKUP, API, check=False)
            print('ROLLBACK=RESTORED_PRE_V43_API', flush=True)
        raise
    finally:
        tmp.unlink(missing_ok=True)

    # Restart only API; V22E standalone service is not restarted or modified.
    run('sudo', 'systemctl', 'restart', SERVICE)
    wait_http('http://127.0.0.1:8000/health', 60)
    print('API_HEALTH=PASS', flush=True)

    # Read the public runtime-mode endpoint and validate both modes via API.
    import json
    def get_json(url):
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode('utf-8'))
    def post(url):
        req = urllib.request.Request(url, data=b'{}', method='POST', headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode('utf-8'))

    current = get_json('http://127.0.0.1:8000/api/v4/runtime-mode')
    original_mode = str(current.get('mode') or 'DAYTRADE').upper()

    normal = post('http://127.0.0.1:8000/api/v4/runtime-mode/NORMAL')
    if int(normal.get('tracker_seconds') or -1) != 60 or int(normal.get('finder_seconds') or -1) != 120 or int(normal.get('korea_tracker_seconds') or -1) != 60:
        raise SystemExit('ABORT NORMAL cadence verification failed: ' + repr(normal))

    day = post('http://127.0.0.1:8000/api/v4/runtime-mode/DAYTRADE')
    if int(day.get('tracker_seconds') or -1) != 5 or int(day.get('finder_seconds') or -1) != 30 or int(day.get('korea_tracker_seconds') or -1) != 5:
        raise SystemExit('ABORT DAYTRADE cadence verification failed: ' + repr(day))

    # Restore the mode that was selected before verification.
    if original_mode in ('NORMAL','DAYTRADE'):
        post(f'http://127.0.0.1:8000/api/v4/runtime-mode/{original_mode}')

    print('NORMAL_TRACKER_SECONDS=60', flush=True)
    print('NORMAL_FINDER_SECONDS=120', flush=True)
    print('DAYTRADE_TRACKER_SECONDS=5', flush=True)
    print('DAYTRADE_FINDER_SECONDS=30', flush=True)
    print('KR_US_CADENCE=UNIFIED', flush=True)
    print('KR_FINDER_300S_FLOOR=REMOVED', flush=True)
    print('STREAMING=ALWAYS_ON', flush=True)
    print('ORDER_AUTHORITY=UNTOUCHED', flush=True)
    print('V22E_SERVICE=UNTOUCHED', flush=True)
    print('DEPLOY=PASS', flush=True)


if __name__ == '__main__':
    main()
