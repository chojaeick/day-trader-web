#!/usr/bin/env python3
"""V156 persist DAYTRADE runtime mode across service restarts.

Runtime patch only. Strategy/order logic unchanged.
Patches /home/ubuntu/day-trader-api/live_server/api.py so runtime_mode initializes
from environment with DAYTRADE as the explicit default for this trading runtime.
Also verifies runtime profile and compiles the file.
"""
from pathlib import Path
import shutil, py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/api.py')
B=P.with_suffix('.py.bak_v156')
S=P.read_text()
if not B.exists(): shutil.copy2(P,B)

old="""runtime_mode={
    'mode':'NORMAL',
    'updated_at':datetime.now(timezone.utc).isoformat(),
}
"""
new="""runtime_mode={
    # V156: persist intended trading runtime across service restarts.
    # Can still be overridden explicitly with DAY_TRADER_RUNTIME_MODE=NORMAL.
    'mode':str(os.getenv('DAY_TRADER_RUNTIME_MODE','DAYTRADE') or 'DAYTRADE').upper(),
    'updated_at':datetime.now(timezone.utc).isoformat(),
}
if runtime_mode['mode'] not in ('NORMAL','DAYTRADE'):
    runtime_mode['mode']='DAYTRADE'
"""

if old in S:
    S=S.replace(old,new,1)
elif 'V156: persist intended trading runtime' not in S:
    print('RUNTIME_MODE_BLOCK_NOT_FOUND')
    raise SystemExit(2)

P.write_text(S)
try:
    py_compile.compile(str(P),doraise=True); comp=True
except Exception as e:
    comp=False; print('COMPILE_ERROR',e)

T=P.read_text()
print('=== V156 PATCH DAYTRADE MODE PERSISTENCE ===')
print('PATCHED',P)
print('BACKUP',B)
print('DEFAULT_DAYTRADE=', "DAY_TRADER_RUNTIME_MODE','DAYTRADE'" in T)
print('NORMAL_OVERRIDE_SUPPORTED=', "DAY_TRADER_RUNTIME_MODE" in T and "('NORMAL','DAYTRADE')" in T)
print('STRATEGY_CHANGE=NONE')
print('ORDER_CHANGE=NONE')
print('PY_COMPILE=', 'PASS' if comp else 'FAIL')
print('NEXT=RESTART_AND_VERIFY_DAYTRADE_PERSISTS')
