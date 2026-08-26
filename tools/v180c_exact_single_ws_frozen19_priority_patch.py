#!/usr/bin/env python3
from pathlib import Path
import shutil, py_compile

K=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
A=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V180C EXACT SINGLE WS FROZEN19 PRIORITY PATCH ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')

for p in (K,A):
    if p.exists():
        b=p.with_name(p.name+'.bak_v180c')
        shutil.copy2(p,b)
        print('BACKUP',b)

s=K.read_text(encoding='utf-8')

# Ensure frozen constant/helper on client class instance exists from V171/V178-era patches.
# Exact current runtime targets observed in V180B output.
old1="registered=tuple(self.s.symbols)"
new1="registered=tuple(dict.fromkeys([*tuple(getattr(self,'frozen_paper_symbols',()) or ()),*tuple(self.s.symbols)]))"
old2="current=tuple(dict.fromkeys([*tuple(self.s.symbols),*tuple(getattr(self,'frozen_paper_symbols',()) or ())]))"
new2="current=tuple(dict.fromkeys([*tuple(getattr(self,'frozen_paper_symbols',()) or ()),*tuple(self.s.symbols)]))"

c1=s.count(old1); c2=s.count(old2)
print('TARGET1_COUNT=',c1)
print('TARGET2_COUNT=',c2)
if c1<1 or c2<1:
    raise SystemExit('EXACT TARGET MISSING; NO WRITE')

s=s.replace(old1,new1,1)
s=s.replace(old2,new2,1)
K.write_text(s,encoding='utf-8')
print('PATCHED',K)

# Verify dedicated frozen websocket task is disabled in api.py after V180 partial patch.
a=A.read_text(encoding='utf-8') if A.exists() else ''
dedicated_enabled=('frozen19_websocket_forever' in a and 'create_task(k.frozen19_websocket_forever' in a and '# V180_DISABLED' not in a)
print('DEDICATED_FROZEN19_TASK_ENABLED=',dedicated_enabled)

py_compile.compile(str(K),doraise=True)
print('PY_COMPILE kiwoom.py PASS')
if A.exists():
    py_compile.compile(str(A),doraise=True)
    print('PY_COMPILE api.py PASS')

check=K.read_text(encoding='utf-8')
print('FROZEN_FIRST_REGISTERED=',new1 in check)
print('FROZEN_FIRST_REFRESH=',new2 in check)
print('SINGLE_USA_WS_ONLY=',not dedicated_enabled)
print('NEXT=V181_RESTART_VERIFY_FROZEN19_PRIORITY_F5_COVERAGE')
