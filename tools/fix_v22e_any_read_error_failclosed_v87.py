#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, sys
ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
print('V87_START=YES')
text=ENGINE.read_text(encoding='utf-8')
bak=ENGINE.with_suffix(f'.py.pre_v87_{int(time.time())}')
shutil.copy2(ENGINE,bak)
old="""    if _read_errors and not out:\n        _holdings_read_ok=False\n        _last_recon=time.monotonic()\n        log('ACCOUNT_FAIL_CLOSED_CACHE_PRESERVED',cached_holdings=len(_holdings_cache),read_errors=_read_errors)\n        return dict(_holdings_cache)\n    _holdings_read_ok=True\n    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)\n"""
new="""    # V87: any account read error makes the entire cycle unknown.\n    # Never accept a partial fallback result as authoritative and never trade on stale cash.\n    if _read_errors:\n        _holdings_read_ok=False\n        _last_recon=time.monotonic()\n        log('ACCOUNT_FAIL_CLOSED_ANY_READ_ERROR',cached_holdings=len(_holdings_cache),partial_holdings=len(out),read_errors=_read_errors)\n        return dict(_holdings_cache)\n    _holdings_read_ok=True\n    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)\n"""
if old not in text:
    print('ABORT=TARGET_BLOCK_NOT_FOUND'); sys.exit(2)
text=text.replace(old,new,1)
ENGINE.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
if r.returncode:
    print('ENGINE_PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,ENGINE); print('ROLLBACK=YES'); sys.exit(3)
print('ENGINE_PY_COMPILE=PASS')
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(4)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
if svc!='active': sys.exit(4)
print('ACCOUNT_READ_FAILURE_POLICY=ANY_ERROR_FAIL_CLOSED')
print('PARTIAL_FALLBACK_AUTHORITY=DISABLED')
print('ORDER_ON_ACCOUNT_READ_ERROR=DISABLED')
print('STALE_CASH_ORDERING=DISABLED_ON_ERROR_CYCLE')
print('DEPLOY=PASS')
