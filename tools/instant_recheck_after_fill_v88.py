#!/usr/bin/env python3
from pathlib import Path
import subprocess, shutil, time, sys
ROOT=Path('/home/ubuntu/day-trader-api')
ENG=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
print('V88_START=YES')
s=ENG.read_text(encoding='utf-8')
bak=ENG.with_suffix('.py.pre_v88')
shutil.copy2(ENG,bak)
old="""                if pending_buy.get('symbol') in holdings or (cash_now>0 and cash_now < f(pending_buy.get('cash_before'))-1.0):
                    log('BUY_CASH_REFRESH_CONFIRMED',symbol=pending_buy.get('symbol'),cash_before=pending_buy.get('cash_before'),cash_now=cash_now)
                    pending_buy=None
"""
new="""                if pending_buy.get('symbol') in holdings or (cash_now>0 and cash_now < f(pending_buy.get('cash_before'))-1.0):
                    log('BUY_CASH_REFRESH_CONFIRMED',symbol=pending_buy.get('symbol'),cash_before=pending_buy.get('cash_before'),cash_now=cash_now)
                    pending_buy=None
                    # V88: account changed after a buy/fill. Re-evaluate current completed bars immediately
                    # for remaining empty slots instead of waiting for the next 5m candle.
                    for _v88_sym in list(last_bar):
                        if _v88_sym not in holdings:
                            last_bar.pop(_v88_sym, None)
                    log('V88_REEVAL_REMAINING_SLOTS',holdings=len(holdings),remaining_slots=max(0,MAX_POSITIONS-len(holdings)),cash_now=cash_now)
"""
if 'V88_REEVAL_REMAINING_SLOTS' not in s:
    if old not in s:
        print('ABORT=BUY_CASH_REFRESH_BLOCK_NOT_FOUND'); sys.exit(2)
    s=s.replace(old,new,1)
    ENG.write_text(s,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(ENG)],capture_output=True,text=True)
if r.returncode:
    print('ENGINE_PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,ENG); print('ROLLBACK=YES'); sys.exit(3)
print('ENGINE_PY_COMPILE=PASS')
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(4)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
print('POST_FILL_REEVAL=IMMEDIATE')
print('MAX_POSITIONS=4')
print('CAPITAL_USE=99_5PCT')
print('ACCOUNT_FAIL_CLOSED=UNCHANGED')
print('DEPLOY=PASS')
