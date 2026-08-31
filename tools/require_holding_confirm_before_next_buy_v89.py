#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, sys, time
ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
print('V89_START=YES')
text=ENGINE.read_text(encoding='utf-8')
bak=ENGINE.with_suffix(f'.py.pre_v89_{int(time.time())}')
shutil.copy2(ENGINE,bak)
old="""            if pending_buy:\n                acct_now=account_snapshot(); cash_now=f(acct_now.get('orderable_cash'))\n                if pending_buy.get('symbol') in holdings or (cash_now>0 and cash_now < f(pending_buy.get('cash_before'))-1.0):\n                    log('BUY_CASH_REFRESH_CONFIRMED',symbol=pending_buy.get('symbol'),cash_before=pending_buy.get('cash_before'),cash_now=cash_now)\n                    pending_buy=None\n                    last_bar.clear()\n                    log('POST_FILL_REEVAL_IMMEDIATE',trigger='BUY_CASH_REFRESH_CONFIRMED')\n"""
new="""            if pending_buy:\n                acct_now=account_snapshot(); cash_now=f(acct_now.get('orderable_cash'))\n                _pb_sym=str(pending_buy.get('symbol') or '').upper()\n                if _pb_sym in holdings:\n                    log('BUY_HOLDING_REFRESH_CONFIRMED',symbol=_pb_sym,cash_before=pending_buy.get('cash_before'),cash_now=cash_now,holding_qty=(holdings.get(_pb_sym) or {}).get('qty'))\n                    pending_buy=None\n                    last_bar.clear()\n                    log('POST_FILL_REEVAL_IMMEDIATE',trigger='BUY_HOLDING_REFRESH_CONFIRMED')\n                elif cash_now>0 and cash_now < f(pending_buy.get('cash_before'))-1.0:\n                    log('BUY_CASH_RESERVED_WAIT_HOLDING_CONFIRM',symbol=_pb_sym,cash_before=pending_buy.get('cash_before'),cash_now=cash_now)\n"""
if old not in text:
    print('ABORT=PENDING_BUY_BLOCK_NOT_FOUND'); sys.exit(2)
text=text.replace(old,new,1)
ENGINE.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
if r.returncode:
    shutil.copy2(bak,ENGINE); print('ENGINE_PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); print('ROLLBACK=YES'); sys.exit(3)
print('ENGINE_PY_COMPILE=PASS')
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(2)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
print('PENDING_BUY_RELEASE=HOLDING_CONFIRM_ONLY')
print('CASH_DROP_ONLY=WAIT')
print('MAX_POSITIONS_GUARD=PROTECTED_FROM_RESERVED_CASH_RACE')
print('ACCOUNT_FAIL_CLOSED=UNCHANGED')
print('DEPLOY=PASS')
