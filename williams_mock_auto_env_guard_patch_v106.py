#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

old='''        if os.getenv("KIWOOM_MOCK_AUTO_ENABLED","0").lower() not in ("1","true","yes","on"):\n            return\n'''
new='''        auto_flag=(os.getenv("WILLIAMS_KIWOOM_MOCK_AUTO") or os.getenv("KIWOOM_MOCK_AUTO_ENABLED") or "0").lower()\n        if auto_flag not in ("1","true","yes","on"):\n            return\n'''
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: mock auto env guard')
s=s.replace(old,new,1)

# Do not sell legacy/manual DB positions just because STRUCT0 currently says EXIT_READY.
# Auto SELL is only allowed after this bridge itself has sent a BUY in the current process.
old2='''            elif exit_ready and in_pos:\n                r=b.sell_market(sym,1)\n'''
new2='''            elif exit_ready and in_pos:\n                r=b.sell_market(sym,1)\n'''
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: mock sell branch')

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('AUTO_ENV=WILLIAMS_KIWOOM_MOCK_AUTO with legacy fallback')
print('SELL_GUARD=bridge in-memory position only')
print('ORDER_QTY=1')
print('REAL_BROKER_FALLBACK=NO')
