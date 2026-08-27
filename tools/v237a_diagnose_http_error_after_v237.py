#!/usr/bin/env python3
import subprocess, urllib.request, urllib.error

print('=== V237A DIAGNOSE HTTP ERROR AFTER V237 ===')
print('READ_ONLY=YES CODE_CHANGE=NONE RESTART=NONE')

# Probe runtime-mode and root without mutating anything.
for url in ['http://127.0.0.1:8000/api/runtime-mode','http://127.0.0.1:8000/']:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            body=r.read(500).decode('utf-8','replace')
            print('HTTP', url, r.status, body)
    except urllib.error.HTTPError as e:
        try:
            body=e.read(500).decode('utf-8','replace')
        except Exception:
            body=''
        print('HTTP_ERROR', url, e.code, body)
    except Exception as e:
        print('HTTP_FAIL', url, type(e).__name__, str(e))

print('=== SERVICE ===')
subprocess.run(['systemctl','is-active','day-trader-api'], check=False)
print('=== PORT8000 ===')
subprocess.run("ss -ltnp | grep ':8000' || true", shell=True, check=False)
print('=== RECENT JOURNAL ERRORS ===')
cmd="journalctl -u day-trader-api -n 120 --no-pager | egrep -i 'Traceback|ERROR|Exception|HTTP/[0-9.]+ [45][0-9][0-9]|Application startup complete|Uvicorn running' | tail -60"
subprocess.run(cmd, shell=True, check=False)
