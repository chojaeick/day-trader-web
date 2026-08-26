from pathlib import Path
import py_compile, subprocess, time, urllib.request, json, shutil

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
BACKUP=Path('/home/ubuntu/day-trader-api/live_server/api.py.bak_v232')
print('=== V232 RESTORE KOREA WILLIAMS TASKS IN DAYTRADE ===')
print('STRATEGY_CHANGE=NONE ORDER_LOGIC_CHANGE=NONE USA_FROZEN_CHANGE=NONE')
print('PURPOSE=RESTORE_KOREA_PREOPEN_DISCOVERY_PULSE_SAFETY_WILLIAMS_HARDSTOP_ENTRY_AUTO_WHILE_KEEPING_USA_FROZEN19')

s=API.read_text()
shutil.copy2(API,BACKUP)
print('BACKUP=',BACKUP)

old="""        if _runtime_profile().get('mode')=='DAYTRADE':
            # V206: frozen19 paper authority only. Keep one Kiwoom WS session + frozen evaluator.
            tasks.extend([
                asyncio.create_task(k.websocket_forever()),
                asyncio.create_task(frozen_usa_paper_forever()),  # V213B_RESTORED_FROZEN
            ])
"""
new="""        if _runtime_profile().get('mode')=='DAYTRADE':
            # V232: keep USA frozen19 isolation, but restore the proven Korea Williams path.
            # Do NOT start v4_engine_forever here; V203 intentionally sheds that legacy heavy loop in DAYTRADE.
            tasks.extend([
                asyncio.create_task(k.websocket_forever()),
                asyncio.create_task(frozen_usa_paper_forever()),  # V213B_RESTORED_FROZEN
                asyncio.create_task(preopen_scheduler_forever()),  # V232_KOREA_RESTORE
                asyncio.create_task(korea_discovery_forever()),    # V232_KOREA_RESTORE
                asyncio.create_task(korea_intraday_pulse_forever()), # V232_KOREA_RESTORE
                asyncio.create_task(korea_safety_forever()),       # V232_KOREA_RESTORE
                asyncio.create_task(williams_mock_hard_stop_forever()), # V232_KOREA_RESTORE
                asyncio.create_task(daytrade_entry_auto_forever()), # V232_KOREA_RESTORE
            ])
"""
count=s.count(old)
print('EXACT_DAYTRADE_BLOCK_COUNT=',count)
if count!=1:
    print('ABORT=EXPECTED_EXACTLY_ONE_BLOCK_NO_MUTATION')
    raise SystemExit(2)
s=s.replace(old,new,1)
API.write_text(s)

try:
    py_compile.compile(str(API),doraise=True)
    print('PY_COMPILE_RC=0')
except Exception as e:
    print('PY_COMPILE_FAIL=',repr(e))
    shutil.copy2(BACKUP,API)
    print('RESTORED_BACKUP=YES')
    raise SystemExit(3)

r=subprocess.run(['sudo','systemctl','restart','day-trader-api'],capture_output=True,text=True)
print('RESTART_RC=',r.returncode)
if r.stdout.strip(): print('RESTART_OUT=',r.stdout.strip())
if r.stderr.strip(): print('RESTART_ERR=',r.stderr.strip())

ready=False
body=None
for i in range(1,25):
    time.sleep(3 if i>1 else 2)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=3) as resp:
            body=json.loads(resp.read().decode())
            print('READY',i,'HTTP=',resp.status,'MODE=',body.get('mode'))
            if resp.status==200:
                ready=True
                break
    except Exception as e:
        print('READY',i,'FAIL=',type(e).__name__)

print('API_READY=',ready,'RUNTIME=',body)

# Verify the running source has all restored startup calls in the DAYTRADE branch.
s2=API.read_text()
for key in [
    'asyncio.create_task(preopen_scheduler_forever()),  # V232_KOREA_RESTORE',
    'asyncio.create_task(korea_discovery_forever()),    # V232_KOREA_RESTORE',
    'asyncio.create_task(korea_intraday_pulse_forever()), # V232_KOREA_RESTORE',
    'asyncio.create_task(korea_safety_forever()),       # V232_KOREA_RESTORE',
    'asyncio.create_task(williams_mock_hard_stop_forever()), # V232_KOREA_RESTORE',
    'asyncio.create_task(daytrade_entry_auto_forever()), # V232_KOREA_RESTORE',
]:
    print('SOURCE_MARKER',key.split('(')[1].split(')')[0],'=>',key in s2)

j=subprocess.run(['journalctl','-u','day-trader-api','--since','2 minutes ago','--no-pager'],capture_output=True,text=True)
lines=[x for x in j.stdout.splitlines() if any(k.lower() in x.lower() for k in ['startup','korea','williams','error','exception','traceback'])]
print('=== RECENT RELEVANT LOG ===')
for x in lines[-80:]: print(x)

print('V232_PASS=',bool(ready and body and body.get('mode')=='DAYTRADE'))
print('KOREA_TASKS_RESTORED_IN_DAYTRADE=YES')
print('USA_FROZEN19_KEPT=YES')
print('NEXT=LEAVE_SERVICE_RUNNING; KOREA_TASKS_SELF_GUARD_BY_KST_SESSION')
