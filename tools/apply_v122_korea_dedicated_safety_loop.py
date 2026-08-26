#!/usr/bin/env python3
"""Apply DAY TRADER V122 dedicated KOREA safety loop.

Runtime target: /home/ubuntu/day-trader-api/live_server/api.py

Why V121 was insufficient:
- V121 moved KOREA work before USA work inside the shared v4_engine_forever loop.
- The first KOREA pass ran, but slow USA warm/tracker work could then hold the same
  loop for a long time, preventing the SECOND and later KOREA safety passes.
- Open Kiwoom mock holdings therefore could still lose continuous V118/V120 exit and
  -1.5% emergency hard-stop monitoring.

V122 behavior:
- create a dedicated korea_safety_forever() asyncio task.
- KOREA finder/tracker cadence is independent of all USA work.
- remove KOREA finder/tracker execution from the shared USA-heavy v4_engine_forever loop
  to avoid duplicate concurrent refreshes.
- dedicated tracker uses asyncio.to_thread exactly like the existing implementation.
- runtime mode and market-open policy remain unchanged.
- V118/V120/V121 strategy and order logic remain unchanged.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile, shutil

ROOT=Path('/home/ubuntu/day-trader-api')
API=ROOT/'live_server'/'api.py'

def fail(msg): raise SystemExit('V122_ABORT: '+msg)

def main():
    print('TARGET_ROOT',ROOT)
    if not API.exists(): fail('missing api.py')
    bak=API.with_name(API.name+'.bak_v122')
    if not bak.exists(): shutil.copy2(API,bak); print('BACKUP',bak)
    s=API.read_text()
    marker='# V122: KOREA safety runs in a dedicated task, isolated from USA latency.'
    if marker in s:
        py_compile.compile(str(API),doraise=True)
        print('API_ALREADY_V122'); print('V122_PATCH_OK'); print('SERVICE_NOT_STARTED'); return

    # Remove the V121 KOREA pass from the shared loop. Keep USA path there.
    old='''            # V121: KOREA safety pass MUST run before any blocking USA work.\n            # Real mock holdings depend on this path for V118/V120 structural exits\n            # and the independent -1.5% hard stop. US API latency/429 must not starve it.\n            if now-last['KOREA']>=max(300,profile['finder_seconds']):\n                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now\n\n            kr_open=False\n            try:\n                kr_open=bool(korea._kst_market_open())\n            except Exception:\n                pass\n            if (profile['mode']=='DAYTRADE' or kr_open) and now-last_tracker['KOREA']>=profile['korea_tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_korea_tracker,korea)\n                last_tracker['KOREA']=time.monotonic()\n\n            # USA discovery/analysis follows the KOREA safety pass.\n'''
    new='''            # V122: KOREA safety runs in a dedicated task, isolated from USA latency.\n            # This shared loop now handles USA work only; do not duplicate KOREA refresh here.\n\n            # USA discovery/analysis follows independently.\n'''
    if old not in s: fail('V121 KOREA block anchor not found')
    s=s.replace(old,new,1)

    # Insert independent KOREA safety loop immediately before existing discovery loop.
    func_anchor='async def korea_discovery_forever():\n'
    if func_anchor not in s: fail('korea_discovery_forever anchor not found')
    helper='''async def korea_safety_forever():\n    """V122: dedicated KOREA finder/tracker safety cadence, independent of USA work."""\n    last_finder=0.0\n    last_tracker=0.0\n    while True:\n        try:\n            profile=_runtime_profile()\n            kr_open=False\n            try:\n                kr_open=bool(korea._kst_market_open())\n            except Exception:\n                pass\n\n            if profile['mode']=='DAYTRADE' or kr_open:\n                now=time.monotonic()\n                if now-last_finder>=max(300,profile['finder_seconds']):\n                    v4.build_korea_finder(korea.discovery,5)\n                    last_finder=time.monotonic()\n                if now-last_tracker>=profile['korea_tracker_seconds']:\n                    await asyncio.to_thread(v4.refresh_korea_tracker,korea)\n                    last_tracker=time.monotonic()\n        except Exception:\n            logging.exception('V122 KOREA safety loop failed')\n        await asyncio.sleep(max(1,min(2,_runtime_profile()['loop_seconds'])))\n\n'''
    s=s.replace(func_anchor,helper+func_anchor,1)

    # Register the dedicated task in lifespan next to the existing KOREA tasks.
    task_old='''                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),\n                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(v4_engine_forever())])\n'''
    task_new='''                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),\n                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),\n                      asyncio.create_task(v4_engine_forever())])\n'''
    if task_old not in s: fail('lifespan task-list anchor not found')
    s=s.replace(task_old,task_new,1)

    API.write_text(s)
    py_compile.compile(str(API),doraise=True)
    print('API_PATCHED')
    print('V122_PATCH_OK')
    print('KOREA_LOOP=DEDICATED_BACKGROUND_TASK')
    print('USA_CAN_NOT_STARVE_KOREA=YES')
    print('KOREA_TRACKER_CADENCE=RUNTIME_PROFILE')
    print('V118_V120_UNCHANGED')
    print('SERVICE_NOT_STARTED')

if __name__=='__main__': main()
