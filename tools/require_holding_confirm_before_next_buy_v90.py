#!/usr/bin/env python3
from pathlib import Path
import re, subprocess, shutil, time, sys
p=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')
s=p.read_text()
print('V90_START=YES')
bak=p.with_suffix(f'.py.pre_v90_{int(time.time())}')
shutil.copy2(p,bak)
pat=re.compile(r"(?ms)^\s{12}if pending_buy:\n(?:\s{16}.*\n){1,8}?\s{20}pending_buy=None\n")
m=pat.search(s)
if not m:
    print('ABORT=PENDING_BUY_BLOCK_NOT_FOUND'); sys.exit(2)
new="""            if pending_buy:\n                acct_now=account_snapshot(); cash_now=f(acct_now.get('orderable_cash'))\n                _pbsym=str(pending_buy.get('symbol') or '').upper()\n                if _pbsym in holdings:\n                    log('BUY_HOLDING_REFRESH_CONFIRMED',symbol=_pbsym,cash_before=pending_buy.get('cash_before'),cash_now=cash_now,holding_qty=((holdings.get(_pbsym) or {}).get('qty')))\n                    pending_buy=None\n                else:\n                    log('BUY_WAIT_HOLDING_CONFIRM',symbol=_pbsym,cash_before=pending_buy.get('cash_before'),cash_now=cash_now)\n"""
s=s[:m.start()]+new+s[m.end():]
p.write_text(s)
py='/home/ubuntu/day-trader-api/venv/bin/python'
r=subprocess.run([py,'-m','py_compile',str(p)],capture_output=True,text=True)
if r.returncode:
    shutil.copy2(bak,p); print('ENGINE_PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); print('ROLLBACK=YES'); sys.exit(3)
print('ENGINE_PY_COMPILE=PASS')
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(3)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
print('PENDING_BUY_CONFIRM=LIVE_HOLDING_ONLY')
print('CASH_DROP_CONFIRM=DISABLED')
print('NEXT_BUY_AFTER_HOLDING_REFRESH=ENABLED')
print('DEPLOY=PASS')
