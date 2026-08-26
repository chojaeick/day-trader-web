#!/usr/bin/env python3
from pathlib import Path
import subprocess, re

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
print('=== V199 EVENT LOOP STARVATION AUDIT ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
print('API_EXISTS=',API.exists())
if not API.exists(): raise SystemExit(2)
src=API.read_text(errors='ignore').splitlines()

# Show all background task starts and the full frozen paper loop region.
for i,line in enumerate(src,1):
    if 'asyncio.create_task(' in line:
        print('TASK',i,line.strip())

start=None
for i,line in enumerate(src,1):
    if line.startswith('async def frozen_usa_paper_forever'):
        start=i; break
if start:
    print('--- FROZEN_LOOP_SOURCE ---')
    for j in range(start, min(len(src), start+150)+1):
        line=src[j-1]
        if j>start and line.startswith('async def '): break
        print(f'{j}: {line}')

# Look for synchronous heavy calls inside async loops across api.py.
print('--- POSSIBLE_BLOCKING_CALLS_IN_ASYNC ---')
in_async=False; async_name=''
for i,line in enumerate(src,1):
    if line.startswith('async def '):
        in_async=True; async_name=line.strip()
    elif in_async and line and not line.startswith((' ','\t','#','@')) and not line.startswith('async def '):
        in_async=False; async_name=''
    if in_async:
        low=line.lower()
        if any(x in low for x in ('requests.','urllib.request','time.sleep(','subprocess.','sqlite3.connect(','db.ticks(','ticks_to_bars(','v4.refresh_','k.discovery','k.quote(')):
            print('BLOCKING_CANDIDATE',i,async_name,'::',line.strip())

# Runtime service/thread/process snapshot.
for cmd in [
    ['systemctl','is-active','day-trader-api.service'],
    ['ps','-eo','pid,ppid,pcpu,pmem,stat,etime,cmd','--sort=-pcpu'],
    ['journalctl','-u','day-trader-api.service','-n','80','--no-pager'],
]:
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        print('CMD',' '.join(cmd),'RC=',r.returncode)
        out=(r.stdout or r.stderr or '')
        if cmd[0]=='ps':
            out='\n'.join(out.splitlines()[:20])
        print(out)
    except Exception as e:
        print('CMD_ERROR',' '.join(cmd),repr(e))

print('NEXT=PATCH_ONLY_CONFIRMED_EVENT_LOOP_BLOCKER_THEN_REVERIFY_RUNTIME_MODE_FROZEN_STATUS')
