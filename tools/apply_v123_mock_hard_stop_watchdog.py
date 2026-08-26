#!/usr/bin/env python3
"""Apply DAY TRADER V123 independent Kiwoom-mock hard-stop watchdog.

Runtime target: /home/ubuntu/day-trader-api/live_server/api.py

Why:
- Williams emergency -1.5% stop previously depended on refresh_korea_tracker().
- Tracker/chart work can block or stall, so a held mock position can escape the stop.

V123 behavior:
- adds a dedicated asyncio background task independent of Finder/Tracker/USA work.
- while runtime mode is DAYTRADE and KRX is open, queries Kiwoom mock kt00004 directly.
- for positive-quantity mock holdings, uses account avg_prc and cur_prc.
- if cur_prc <= avg_prc * 0.985, submits a market SELL through KiwoomMockBroker.
- broker V116 order throttling remains authoritative.
- a 60-second per-symbol pending guard prevents duplicate sell spam while account state settles.
- watchdog applies only to the Kiwoom MOCK account and only when mock order_enable is on.
- no production credentials/endpoints are used.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile, shutil

ROOT=Path('/home/ubuntu/day-trader-api')
API=ROOT/'live_server'/'api.py'

def fail(msg): raise SystemExit('V123_ABORT: '+msg)

def main():
    print('TARGET_ROOT',ROOT)
    if not API.exists(): fail('missing api.py')
    bak=API.with_name(API.name+'.bak_v123')
    if not bak.exists(): shutil.copy2(API,bak); print('BACKUP',bak)
    s=API.read_text()
    marker='# V123: independent mock-account emergency hard-stop watchdog.'
    if marker in s:
        py_compile.compile(str(API),doraise=True)
        print('API_ALREADY_V123'); print('V123_PATCH_OK'); print('SERVICE_NOT_STARTED'); return

    insert_anchor='async def korea_discovery_forever():\n'
    if insert_anchor not in s: fail('korea_discovery_forever anchor not found')

    helper=r'''# V123: independent mock-account emergency hard-stop watchdog.
async def williams_mock_hard_stop_forever():
    """Protect Kiwoom MOCK holdings even when tracker/chart work is blocked."""
    import time as _time
    pending={}
    while True:
        try:
            profile=_runtime_profile()
            kr_open=False
            try:
                kr_open=bool(korea._kst_market_open())
            except Exception:
                pass
            if profile.get('mode')=='DAYTRADE' and kr_open:
                from live_server.kiwoom_mock_broker import KiwoomMockBroker
                b=KiwoomMockBroker()
                if b.cfg.order_enable:
                    bal=await asyncio.to_thread(
                        b.request_account,
                        'kt00004',
                        {'qry_tp':'0','dmst_stex_tp':'KRX'}
                    )
                    now=_time.monotonic()
                    live=set()
                    for x in (bal.get('stk_acnt_evlt_prst') or []):
                        sym=str(x.get('stk_cd') or '').replace('A','').zfill(6)
                        try: qty=int(str(x.get('rmnd_qty') or '0').replace(',',''))
                        except Exception: qty=0
                        try: avg=float(str(x.get('avg_prc') or '0').replace(',',''))
                        except Exception: avg=0.0
                        try: cur=abs(float(str(x.get('cur_prc') or '0').replace(',','')))
                        except Exception: cur=0.0
                        if not sym or qty<=0:
                            continue
                        live.add(sym)
                        if avg<=0 or cur<=0 or cur>avg*0.985:
                            continue
                        last=float(pending.get(sym,0.0) or 0.0)
                        if last and (now-last)<60.0:
                            continue
                        r=await asyncio.to_thread(b.sell_market,sym,qty)
                        pending[sym]=_time.monotonic()
                        logging.warning(
                            'WILLIAMS_MOCK_HARD_STOP_WATCHDOG_SELL sym=%s qty=%s avg=%s cur=%s loss_pct=%.4f order_no=%s',
                            sym,qty,avg,cur,((cur/avg)-1.0)*100.0,
                            r.get('ord_no') or r.get('order_no')
                        )
                    for sym in list(pending):
                        if sym not in live:
                            pending.pop(sym,None)
        except Exception:
            logging.exception('V123 mock hard-stop watchdog failed')
        await asyncio.sleep(5)

'''
    s=s.replace(insert_anchor,helper+insert_anchor,1)

    # Add watchdog to the existing lifespan task bundle immediately next to korea_safety_forever.
    old="""                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
                      asyncio.create_task(v4_engine_forever())])
"""
    new="""                      asyncio.create_task(preopen_scheduler_forever()),asyncio.create_task(korea_discovery_forever()),
                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),
                      asyncio.create_task(williams_mock_hard_stop_forever()),
                      asyncio.create_task(v4_engine_forever())])
"""
    if old not in s: fail('lifespan task bundle anchor not found')
    s=s.replace(old,new,1)

    API.write_text(s)
    py_compile.compile(str(API),doraise=True)
    print('API_PATCHED')
    print('V123_PATCH_OK')
    print('HARD_STOP_SOURCE=KIWOOM_MOCK_KT00004_DIRECT')
    print('HARD_STOP_THRESHOLD=-1.5%')
    print('WATCHDOG_CADENCE_SEC=5')
    print('DUPLICATE_SELL_GUARD_SEC=60')
    print('TRACKER_DEPENDENCY=NONE')
    print('SERVICE_NOT_STARTED')

if __name__=='__main__': main()
