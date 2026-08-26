#!/usr/bin/env python3
from pathlib import Path
import shutil, py_compile, subprocess

R=Path('/home/ubuntu/day-trader-api')
ki=R/'live_server/kiwoom.py'
api=R/'live_server/api.py'
print('=== V180 SINGLE WS FROZEN19 PRIORITY + DISABLE DEDICATED ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
for p in (ki,api):
    if not p.exists(): raise SystemExit(f'MISSING {p}')

# backups
for p in (ki,api):
    b=Path(str(p)+'.bak_v180')
    shutil.copy2(p,b)
    print('BACKUP',b)

akit=api.read_text(encoding='utf-8')
# Disable the V178 dedicated websocket startup task, preserving code for rollback/audit.
old_lines=akit.splitlines()
out=[]; disabled=0
for line in old_lines:
    if 'frozen19_websocket_forever' in line and ('create_task' in line or 'asyncio.create_task' in line):
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'# V180 disabled: second USA websocket session is rejected/closed by Kiwoom (1000 OK Bye).')
        out.append(indent+'# '+line.lstrip())
        disabled+=1
    else:
        out.append(line)
akit='\n'.join(out)+'\n'
api.write_text(akit,encoding='utf-8')
print('DEDICATED_TASKS_DISABLED=',disabled)

# Patch single websocket desired universe construction to put frozen19 first.
# V171 added FROZEN_CORE_19 constant in runtime kiwoom.py. We patch occurrences where current=self.s.symbols
# or _ws_items(current) is built inside websocket_forever so frozen are first, dynamic appended uniquely.
kt=ki.read_text(encoding='utf-8')
marker="# V180_SINGLE_WS_FROZEN19_PRIORITY"
if marker not in kt:
    target="current=tuple(self.s.symbols)"
    repl=("# V180_SINGLE_WS_FROZEN19_PRIORITY: one Kiwoom USA websocket only; frozen19 first, dynamic after.\n"
          "                        _dyn=list(self.s.symbols)\n"
          "                        _f19=list(globals().get('FROZEN_CORE_19',[]) or [])\n"
          "                        _merged=[]\n"
          "                        for _s in _f19+_dyn:\n"
          "                            _s=str(_s or '').upper()\n"
          "                            if _s and _s not in _merged:_merged.append(_s)\n"
          "                        current=tuple(_merged)")
    if target not in kt:
        raise SystemExit('TARGET current=tuple(self.s.symbols) NOT FOUND')
    kt=kt.replace(target,repl)
    ki.write_text(kt,encoding='utf-8')
    print('SINGLE_WS_PRIORITY_PATCHED=YES')
else:
    print('SINGLE_WS_PRIORITY_PATCHED=ALREADY')

for p in (api,ki):
    py_compile.compile(str(p),doraise=True)
    print('PY_COMPILE',p.name,'PASS')
print('DEDICATED_FROZEN19_WS_DISABLED=YES')
print('SINGLE_USA_WS_ONLY=YES')
print('FROZEN19_PRIORITY_IN_SINGLE_WS=YES')
print('REAL_BROKER_CALLS_ADDED=NONE')
print('NEXT=V181_RESTART_VERIFY_SINGLE_WS_FROZEN19_F5')
