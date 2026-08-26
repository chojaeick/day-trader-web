#!/usr/bin/env python3
"""Apply DAY TRADER V121 KOREA safety-priority loop ordering.

Runtime target: /home/ubuntu/day-trader-api/live_server/api.py

Observed failure:
- DAYTRADE mode entered the shared V4 engine loop.
- USA Finder warm_usa_symbols() and USA tracker work ran before KOREA tracker.
- Slow/limited US API work could therefore delay KOREA tracker for minutes.
- During that delay real Kiwoom mock holdings were not reaching V118/V120 exit
  monitoring, including the independent -1.5% emergency hard stop.

V121 behavior:
- in DAYTRADE shared loop, process KOREA Finder and KOREA Tracker first.
- only after the KOREA safety pass completes, run USA Finder/warmup and USA Tracker.
- existing cadences, strategy logic, V118/V120 behavior, and USA logic are unchanged.
- bridge candidate warm task remains asynchronous as before.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile
import shutil

ROOT=Path('/home/ubuntu/day-trader-api')
API=ROOT/'live_server'/'api.py'

def fail(msg):
    raise SystemExit('V121_ABORT: '+msg)

def main():
    print('TARGET_ROOT',ROOT)
    if not API.exists(): fail('missing api.py')
    bak=API.with_name(API.name+'.bak_v121')
    if not bak.exists():
        shutil.copy2(API,bak)
        print('BACKUP',bak)
    s=API.read_text()
    marker='# V121: KOREA safety pass MUST run before any blocking USA work.'
    if marker in s:
        py_compile.compile(str(API),doraise=True)
        print('API_ALREADY_V121')
        print('V121_PATCH_OK')
        print('SERVICE_NOT_STARTED')
        return

    old='''            if now-last['USA']>=profile['finder_seconds']:\n                usa_candidates=screener_rows(db.quotes(),db.daily_metrics(),40)\n                finder=v4.build_usa_finder(\n                    usa_candidates,\n                    k.discovery,5,db=db\n                )\n                # Candidate data warming runs asynchronously so live Finder/Tracker\n                # is never blocked by extra quote/daily/minute requests.\n                if bridge_warm_task is None or bridge_warm_task.done():\n                    bridge_warm_task=asyncio.create_task(\n                        warm_bridge_candidates(usa_candidates,k.discovery)\n                    )\n                finder_syms=[r.get('symbol') for r in (finder.get('rows') or [])]\n                light_syms=[r.get('symbol') for r in (finder.get('light_rows') or [])]\n                await warm_usa_symbols(finder_syms)\n                logging.info(\n                    'V4 light tracker: %s',\n                    ','.join(x for x in light_syms[:20] if x)\n                )\n                # Keep the cache bounded to names that are still relevant plus positions.\n                active=set(finder_syms)\n                try:\n                    active.update(p.get('symbol') for p in v4.store.positions('USA') if p.get('symbol'))\n                except Exception:\n                    pass\n                warmed_usa.intersection_update(active)\n                last['USA']=now\n\n            if now-last['KOREA']>=max(300,profile['finder_seconds']):\n                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now\n\n            # Heavy analysis is cadence-controlled. Streaming and Kiwoom\n            # connectivity are NOT affected by runtime mode.\n            if now-last_tracker['USA']>=profile['tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_usa_tracker,db)\n                last_tracker['USA']=time.monotonic()\n\n            # Do not burn CPU on the closed Korean market in NORMAL mode.\n            kr_open=False\n            try:\n                kr_open=bool(korea._kst_market_open())\n            except Exception:\n                pass\n            if (profile['mode']=='DAYTRADE' or kr_open) and now-last_tracker['KOREA']>=profile['korea_tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_korea_tracker,korea)\n                last_tracker['KOREA']=time.monotonic()\n'''

    new='''            # V121: KOREA safety pass MUST run before any blocking USA work.\n            # Real mock holdings depend on this path for V118/V120 structural exits\n            # and the independent -1.5% hard stop. US API latency/429 must not starve it.\n            if now-last['KOREA']>=max(300,profile['finder_seconds']):\n                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now\n\n            kr_open=False\n            try:\n                kr_open=bool(korea._kst_market_open())\n            except Exception:\n                pass\n            if (profile['mode']=='DAYTRADE' or kr_open) and now-last_tracker['KOREA']>=profile['korea_tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_korea_tracker,korea)\n                last_tracker['KOREA']=time.monotonic()\n\n            # USA discovery/analysis follows the KOREA safety pass.\n            if now-last['USA']>=profile['finder_seconds']:\n                usa_candidates=screener_rows(db.quotes(),db.daily_metrics(),40)\n                finder=v4.build_usa_finder(\n                    usa_candidates,\n                    k.discovery,5,db=db\n                )\n                # Candidate data warming runs asynchronously so subsequent loops are not\n                # blocked by the bridge warm task itself.\n                if bridge_warm_task is None or bridge_warm_task.done():\n                    bridge_warm_task=asyncio.create_task(\n                        warm_bridge_candidates(usa_candidates,k.discovery)\n                    )\n                finder_syms=[r.get('symbol') for r in (finder.get('rows') or [])]\n                light_syms=[r.get('symbol') for r in (finder.get('light_rows') or [])]\n                await warm_usa_symbols(finder_syms)\n                logging.info(\n                    'V4 light tracker: %s',\n                    ','.join(x for x in light_syms[:20] if x)\n                )\n                active=set(finder_syms)\n                try:\n                    active.update(p.get('symbol') for p in v4.store.positions('USA') if p.get('symbol'))\n                except Exception:\n                    pass\n                warmed_usa.intersection_update(active)\n                last['USA']=now\n\n            # Heavy USA analysis is cadence-controlled. Streaming and Kiwoom\n            # connectivity are NOT affected by runtime mode.\n            if now-last_tracker['USA']>=profile['tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_usa_tracker,db)\n                last_tracker['USA']=time.monotonic()\n'''

    if old not in s:
        fail('verified V4 engine ordering anchor not found')
    s=s.replace(old,new,1)
    API.write_text(s)
    py_compile.compile(str(API),doraise=True)
    print('API_PATCHED')
    print('V121_PATCH_OK')
    print('LOOP_ORDER=KOREA_FINDER_KOREA_TRACKER_THEN_USA')
    print('KOREA_HARD_STOP_PRIORITY=YES')
    print('V118_V120_UNCHANGED')
    print('SERVICE_NOT_STARTED')

if __name__=='__main__':
    main()
