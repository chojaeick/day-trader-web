#!/usr/bin/env python3
from pathlib import Path
import py_compile, shutil, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
KIW=Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')
print('=== V180D DISABLE DEDICATED FROZEN19 TASK EXACT ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')

for p in (API,KIW):
    if not p.exists():
        raise SystemExit(f'MISSING {p}')

api=API.read_text(encoding='utf-8')
kiw=KIW.read_text(encoding='utf-8')
shutil.copy2(API, str(API)+'.bak_v180d')
print('BACKUP',str(API)+'.bak_v180d')

# Find task-creation lines that reference the dedicated frozen websocket coroutine.
lines=api.splitlines()
matched=[]
out=[]
for i,line in enumerate(lines,1):
    low=line.lower()
    if ('frozen19' in low or 'frozen_19' in low) and ('create_task' in low or 'websocket' in low) and not line.lstrip().startswith('#'):
        matched.append((i,line))
        indent=line[:len(line)-len(line.lstrip())]
        out.append(indent+'# V180D_DISABLED_DEDICATED_FROZEN19_TASK: '+line.lstrip())
    else:
        out.append(line)

# Also catch create_task(k.<method>) where the method name contains frozen but no 19.
if not matched:
    out=[]
    for i,line in enumerate(lines,1):
        low=line.lower()
        if 'create_task' in low and 'frozen' in low and 'websocket' in low and not line.lstrip().startswith('#'):
            matched.append((i,line))
            indent=line[:len(line)-len(line.lstrip())]
            out.append(indent+'# V180D_DISABLED_DEDICATED_FROZEN19_TASK: '+line.lstrip())
        else:
            out.append(line)

print('DEDICATED_TASK_MATCHES=',len(matched))
for m in matched: print('MATCH',m)
if len(matched)!=1:
    raise SystemExit('EXPECTED_EXACTLY_ONE_DEDICATED_TASK_MATCH')

API.write_text('\n'.join(out)+'\n',encoding='utf-8')

for p in (API,KIW):
    py_compile.compile(str(p),doraise=True)
    print('PY_COMPILE',p.name,'PASS')

api2=API.read_text(encoding='utf-8')
kiw2=KIW.read_text(encoding='utf-8')
enabled=[]
for i,line in enumerate(api2.splitlines(),1):
    low=line.lower()
    if ('frozen19' in low or 'frozen_19' in low or ('frozen' in low and 'websocket' in low)) and 'create_task' in low and not line.lstrip().startswith('#'):
        enabled.append((i,line))
print('DEDICATED_FROZEN19_TASK_ENABLED=',bool(enabled))
print('ENABLED_MATCHES=',enabled)
print('FROZEN_FIRST_REGISTERED=', "registered=tuple(dict.fromkeys([*tuple(getattr(self,'frozen_paper_symbols',()) or ()),*tuple(self.s.symbols)]))" in kiw2)
print('FROZEN_FIRST_REFRESH=', "current=tuple(dict.fromkeys([*tuple(getattr(self,'frozen_paper_symbols',()) or ()),*tuple(self.s.symbols)]))" in kiw2)
print('SINGLE_USA_WS_ONLY=',not bool(enabled))
print('NEXT=V181_RESTART_VERIFY_SINGLE_WS_FROZEN19_PRIORITY_F5_COVERAGE')
