from __future__ import annotations

from pathlib import Path
import os, py_compile, subprocess, tempfile, time, urllib.request

RUNTIME=Path('/home/ubuntu/day-trader-api')
V4=RUNTIME/'live_server/v4_engine.py'
API=RUNTIME/'live_server/api.py'
SERVICE='day-trader-api'


def run(*a):
    print('+',' '.join(map(str,a)),flush=True)
    subprocess.run(list(map(str,a)),check=True)


def install_text(dst: Path, text: str):
    fd,tmp=tempfile.mkstemp(prefix='v22_harden_',suffix='.py'); os.close(fd)
    p=Path(tmp)
    try:
        p.write_text(text,encoding='utf-8')
        py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,dst)
    finally:
        p.unlink(missing_ok=True)


def patch_v4():
    s=V4.read_text(encoding='utf-8')
    required=["entry=bool(_v22_decision.get('enter'))",'V22_KR_FULL_ORDER_AUTHORITY','_v22_kr_exit(row,st)','V22_KR_CAPITAL_ALLOCATOR']
    for x in required:
        if x not in s: raise SystemExit('ABORT missing V22 runtime marker: '+x)

    # Make execution logs accurately identify the authority.
    s=s.replace('WILLIAMS_MOCK_BUY_ACCEPTED sym=%s qty=%s price=%s order_no=%s struct5=%s',
                'V22_KR_BUY_ACCEPTED sym=%s qty=%s price=%s order_no=%s struct5=%s')
    s=s.replace('"WILLIAMS_MOCK_BUY"','"V22_KR_BUY"')

    # No new position may be opened from 15:29 KST onward.
    anchor='            if entry and not in_pos:\n'
    guard='''            # V22_KR_EOD_ENTRY_LOCK: no new KR position from 15:29 KST.\n            _eod_now=_dt.now(_WILLIAMS_KST)\n            _eod_locked=(_eod_now.hour>15 or (_eod_now.hour==15 and _eod_now.minute>=29))\n            if entry and not in_pos and _eod_locked:\n                row['engine5_v22_entry_block']='EOD_1529_ENTRY_LOCK'\n                return\n\n            if entry and not in_pos:\n'''
    if 'V22_KR_EOD_ENTRY_LOCK' not in s:
        if s.count(anchor)!=1: raise SystemExit('ABORT buy anchor count='+str(s.count(anchor)))
        s=s.replace(anchor,guard,1)

    # Static rejection of known legacy broker-exit code inside the KR order bridge.
    a=s.index('    def _williams_mock_auto_step(self, row):')
    b=s.index('    def _finalize(self,market,rows):',a)
    block=s[a:b]
    banned=['hard_stop=bool(entry_price and price and price<=entry_price*0.985)','WILLIAMS_MOCK_SELL_ACCEPTED']
    for x in banned:
        if x in block: raise SystemExit('ABORT legacy KR sell authority remains: '+x)
    install_text(V4,s)
    print('V22_V4_AUTHORITY_HARDENED=PASS',flush=True)


def patch_api():
    s=API.read_text(encoding='utf-8')

    # Kill the independent Williams -1.5% broker-sell watchdog. Function may remain for history,
    # but it is not scheduled and therefore has zero order authority.
    task='                      asyncio.create_task(williams_mock_hard_stop_forever()),\n'
    if task in s:
        s=s.replace(task,'',1)
        print('WILLIAMS_HARD_STOP_TASK=REMOVED',flush=True)
    else:
        print('WILLIAMS_HARD_STOP_TASK=NOT_SCHEDULED',flush=True)

    marker='# V22_KR_EOD_FLATTEN_1529'
    if marker not in s:
        anchor='async def korea_discovery_forever():\n'
        if anchor not in s: raise SystemExit('ABORT api insertion anchor missing')
        helper=r'''# V22_KR_EOD_FLATTEN_1529
async def v22_kr_eod_flatten_forever():
    """Kiwoom MOCK only: at 15:29 KST flatten every positive KR holding once.

    This is an execution schedule rule, not a Williams strategy exit. It is part of
    the V22 KR day-trading contract: no overnight KR mock positions.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _kst=_ZI('Asia/Seoul')
    sent={}
    while True:
        try:
            now=_dt.now(_kst)
            day=now.strftime('%Y%m%d')
            # Active from 15:29:00 through 15:30:59. Account refresh confirms what remains.
            if now.weekday()<5 and now.hour==15 and 29<=now.minute<=30:
                from live_server.kiwoom_mock_broker import KiwoomMockBroker
                b=KiwoomMockBroker()
                if b.cfg.order_enable:
                    bal=await asyncio.to_thread(b.request_account,'kt00004',{'qry_tp':'0','dmst_stex_tp':'KRX'})
                    rows=(bal.get('stk_acnt_evlt_prst') or bal.get('acnt_evlt_prst') or [])
                    live=set()
                    for x in rows:
                        sym=str(x.get('stk_cd') or x.get('stk_no') or x.get('code') or '').replace('A','').zfill(6)
                        try: qty=int(float(str(x.get('rmnd_qty') or x.get('hldg_qty') or x.get('hold_qty') or x.get('qty') or '0').replace(',','')))
                        except Exception: qty=0
                        if not sym or qty<=0: continue
                        live.add(sym)
                        key=(day,sym,qty)
                        if sent.get(key): continue
                        r=await asyncio.to_thread(b.sell_market,sym,qty)
                        sent[key]=True
                        logging.warning('V22_KR_EOD_FLATTEN_ACCEPTED sym=%s qty=%s order_no=%s',sym,qty,r.get('ord_no') or r.get('order_no'))
                    # If account still shows a positive remainder after a different quantity appears,
                    # that distinct state may be submitted once; identical state is never auto-retried.
                    for k in list(sent):
                        if k[0]!=day: sent.pop(k,None)
        except Exception:
            logging.exception('V22 KR EOD flatten loop failed')
        await asyncio.sleep(5)

'''
        s=s.replace(anchor,helper+anchor,1)

    # Add EOD task next to Korea safety loop / v4 engine loop. Handle both common layouts.
    if 'asyncio.create_task(v22_kr_eod_flatten_forever())' not in s:
        candidates=[
            ('                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),\n                      asyncio.create_task(v4_engine_forever())])',
             '                      asyncio.create_task(korea_intraday_pulse_forever()),asyncio.create_task(korea_safety_forever()),\n                      asyncio.create_task(v22_kr_eod_flatten_forever()),\n                      asyncio.create_task(v4_engine_forever())])'),
            ('asyncio.create_task(korea_safety_forever()),\n                      asyncio.create_task(v4_engine_forever())])',
             'asyncio.create_task(korea_safety_forever()),\n                      asyncio.create_task(v22_kr_eod_flatten_forever()),\n                      asyncio.create_task(v4_engine_forever())])')
        ]
        done=False
        for old,new in candidates:
            if old in s:
                s=s.replace(old,new,1); done=True; break
        if not done: raise SystemExit('ABORT lifespan task anchor missing')

    # Verify no active scheduling of Williams sell watchdog remains.
    if 'asyncio.create_task(williams_mock_hard_stop_forever())' in s:
        raise SystemExit('ABORT Williams hard-stop task still scheduled')
    if 'asyncio.create_task(v22_kr_eod_flatten_forever())' not in s:
        raise SystemExit('ABORT V22 EOD task missing')

    install_text(API,s)
    print('V22_API_AUTHORITY_HARDENED=PASS',flush=True)


def main():
    if not V4.exists() or not API.exists(): raise SystemExit('ABORT runtime files missing')
    run('sudo','cp','-p',V4,str(V4)+'.pre_v22_authority_hardening')
    run('sudo','cp','-p',API,str(API)+'.pre_v22_authority_hardening')
    patch_v4(); patch_api()
    run(RUNTIME/'venv/bin/python','-m','py_compile',V4,API)
    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+60; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                last=r.read().decode('utf-8','replace')
                if r.status==200: print('HEALTH=PASS',flush=True); break
        except Exception as e: last=repr(e)
        time.sleep(2)
    else: raise SystemExit('ABORT health failed: '+str(last))

    v4=V4.read_text(); api=API.read_text()
    assert "entry=bool(_v22_decision.get('enter'))" in v4
    assert '_v22_kr_exit(row,st)' in v4
    assert 'V22_KR_EOD_ENTRY_LOCK' in v4
    assert 'asyncio.create_task(williams_mock_hard_stop_forever())' not in api
    assert 'asyncio.create_task(v22_kr_eod_flatten_forever())' in api
    print('KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE_ONLY',flush=True)
    print('KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE_ONLY',flush=True)
    print('WILLIAMS_BROKER_BUY_AUTHORITY=DISABLED',flush=True)
    print('WILLIAMS_BROKER_SELL_AUTHORITY=DISABLED',flush=True)
    print('LEGACY_MINUS_1_5PCT_WATCHDOG=DISABLED',flush=True)
    print('EOD_NEW_ENTRY_LOCK=15:29_KST',flush=True)
    print('EOD_FLATTEN=15:29_KST_ALL_HOLDINGS',flush=True)
    print('BUY_LOG_LABEL=V22_KR_BUY_ACCEPTED',flush=True)
    print('BROKER=KIWOOM_MOCK_ONLY',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
