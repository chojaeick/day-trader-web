#!/usr/bin/env python3
from pathlib import Path
import os, subprocess, sys, time, urllib.request, json, shutil

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
ENV=Path('/home/ubuntu/day-trader-api/.env')
BACKUP=RUNTIME.with_name('v4_engine.py.bak_v234d')

print('=== V234D RESTORE RUNTIME V233+V234 GUARDS THEN REENABLE KOREA MOCK ===')
print('REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')
print('FAIL_CLOSED=YES')

if not RUNTIME.exists():
    print('FAIL=RUNTIME_NOT_FOUND'); sys.exit(2)
s=RUNTIME.read_text()
shutil.copy2(RUNTIME,BACKUP)
print('BACKUP=',BACKUP)

# Guard marker used by V233/V234C checks. Reinsert directly in the real mock buy path.
needle='''            if entry and not in_pos:\n'''
marker='''            # V233_STRUCT5_PRICE_GUARD: order-time price must still be above the detected STRUCT5 resistance.\n            if entry and not in_pos and bool(row.get("williams_struct5_signal")):\n                _s5_res=_f(row.get("williams_struct5_resistance"))\n                _live_px=_f(row.get("price"))\n                if _s5_res>0 and _live_px<=_s5_res:\n                    return\n'''
if 'V233_STRUCT5_PRICE_GUARD' not in s:
    if needle not in s:
        print('FAIL=MOCK_ENTRY_ANCHOR_NOT_FOUND'); sys.exit(3)
    s=s.replace(needle, marker+needle, 1)
    print('RESTORE_V233_GUARD=1')
else:
    print('RESTORE_V233_GUARD=0_ALREADY_PRESENT')

# V234 marker must already exist; do not invent a second MTF implementation here.
if 'V234_MTF_ENTRY_GUARD' not in s:
    print('FAIL=V234_MTF_GUARD_MISSING')
    sys.exit(4)

RUNTIME.write_text(s)
rc=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(RUNTIME)]).returncode
print('PY_COMPILE_RC=',rc)
if rc!=0: sys.exit(5)

s2=RUNTIME.read_text()
checks={
 'V233_STRUCT5_PRICE_GUARD':'V233_STRUCT5_PRICE_GUARD' in s2,
 'V234_MTF_GUARD':'V234_MTF_ENTRY_GUARD' in s2,
 'MOCK_BUY_PATH':'buy_market(sym,qty)' in s2,
 'MOCK_SELL_PATH':'sell_market(sym,qty)' in s2,
}
print('STATIC_CHECKS=',checks)
if not all(checks.values()):
    print('V234D_PASS= False'); print('KOREA_MOCK_AUTO_RUNNING=NO'); sys.exit(6)

# Enable only the mock-auto flag. Preserve all other env content and secrets.
if not ENV.exists():
    print('FAIL=ENV_NOT_FOUND'); sys.exit(7)
lines=ENV.read_text().splitlines()
found=False; out=[]
for line in lines:
    if line.startswith('WILLIAMS_KIWOOM_MOCK_AUTO='):
        out.append('WILLIAMS_KIWOOM_MOCK_AUTO=1'); found=True
    else:
        out.append(line)
if not found: out.append('WILLIAMS_KIWOOM_MOCK_AUTO=1')
ENV.write_text('\n'.join(out)+'\n')

rr=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rr)
if rr!=0: sys.exit(8)

ready=False
for i in range(1,21):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=3) as r:
            data=json.loads(r.read().decode())
            print(f'READY {i} HTTP=',r.status,'MODE=',data.get('mode'))
            ready=True; break
    except Exception as e:
        print(f'READY {i} FAIL=',type(e).__name__)
        time.sleep(2)
print('API_READY=',ready)
active=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True).stdout.strip()
print('SERVICE_ACTIVE=',active)

# Final env check without printing secrets.
env_on=False
for line in ENV.read_text().splitlines():
    if line.strip().lower()=='williams_kiwoom_mock_auto=1': env_on=True

ok=ready and active=='active' and env_on and all(checks.values())
print('V234D_PASS=',ok)
print('KOREA_MOCK_AUTO_RUNNING=' + ('YES' if ok else 'NO'))
print('BASELINE=ONLY_TRADES_AFTER_V234D_COUNT_AS_NEW_MTF_GUARD_RUN')
sys.exit(0 if ok else 9)
