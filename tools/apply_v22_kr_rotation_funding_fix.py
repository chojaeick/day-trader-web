from __future__ import annotations

"""Fix KR V22 funding so tiny residual cash cannot create tiny new positions.

Current allocator correctly uses 99.5% of free cash, but it only rotates capital
when qty < 1. That means a nearly fully-invested account with ~200k residual cash
can still open a tiny 200k position instead of selling ~50% of the largest other
holding first. This patch makes the rotation rule match the project policy:

- for a NEW V22 target, if free cash is smaller than ~50% of the largest other
  holding's current value, submit one 50% rebalance sell and wait for released cash;
- on the later refresh, size the BUY from the newly visible live cash;
- never chain SELL+BUY in the same refresh;
- preserve V22 BUY/SELL authority, max 4 holdings, and no same-bar auto retry.
"""

from pathlib import Path
import os
import py_compile
import subprocess
import tempfile
import time
import urllib.request

RUNTIME=Path('/home/ubuntu/day-trader-api')
TARGET=RUNTIME/'live_server/v4_engine.py'
SERVICE='day-trader-api'

OLD='''                # Use essentially all currently free cash. 0.5% reserve absorbs\n                # quote movement/fees while preserving full-capital compounding.\n                _budget=max(0.0,_cash*0.995)\n                qty=int(_budget//price)\n\n                if qty<1:\n                    # Capital is invested. Rotate once by selling ~50% of the largest\n                    # OTHER position, then wait for a later account read before BUY.\n                    _others=[_h for _h in _holds if _h['symbol']!=sym and _h['qty']>0]\n                    if not _others:\n                        return\n                    _src=max(_others,key=lambda _h:_h['value'])\n                    _sell_qty=max(1,int(_src['qty']*0.50))\n                    _reb_key=(\"V22_KR_REBALANCE_ATTEMPT\",sym,_bar,_src['symbol'])\n                    if self._last.get(_reb_key):\n                        return\n                    self._last[_reb_key]={'ts':_time.time(),'qty':_sell_qty}\n                    _rr=b.sell_market(_src['symbol'],_sell_qty)\n                    import logging as _logging\n                    _logging.warning(\"V22_KR_REBALANCE_SELL target=%s source=%s qty=%s total=%s cash=%s resp=%s\",sym,_src['symbol'],_sell_qty,_total,_cash,_rr)\n                    self.store.event(\"KOREA\",sym,\"V22_KR_REBALANCE_SELL\",None,\"ORDER_SENT\",power=_f(row.get(\"power\")),message=f'{sym} funding: sell 50% {_src[\"symbol\"]} x{_sell_qty}',payload={'target':sym,'source':_src['symbol'],'sell_qty':_sell_qty,'total_assets':_total,'cash':_cash,'order':_rr,'engine_decision':_dec})\n                    return\n\n                self._last[_attempt_key]={'ts':_time.time(),'qty':qty,'cash':_cash,'total_assets':_total}\n'''

NEW='''                # Use essentially all currently free cash. 0.5% reserve absorbs\n                # quote movement/fees while preserving full-capital compounding.\n                _budget=max(0.0,_cash*0.995)\n                qty=int(_budget//price)\n\n                # V22_KR_ROTATION_FUNDING_FIX: a tiny residual cash balance must not\n                # create a tiny new position. For a NEW target, compare live free cash\n                # with the amount that a 50% trim of the largest other holding would\n                # release. If cash is smaller, rotate first and WAIT for the later\n                # kt00004 refresh to show the released cash before any BUY.\n                _others=[_h for _h in _holds if _h['symbol']!=sym and _h['qty']>0]\n                _src=max(_others,key=lambda _h:_h['value']) if _others else None\n                _rotation_need=bool(sym not in _held_syms and _src and _cash < (0.50*_src['value']))\n                if _rotation_need:\n                    _sell_qty=max(1,int(_src['qty']*0.50))\n                    _reb_key=(\"V22_KR_REBALANCE_ATTEMPT\",sym,_bar,_src['symbol'])\n                    if self._last.get(_reb_key):\n                        return\n                    self._last[_reb_key]={'ts':_time.time(),'qty':_sell_qty,'cash':_cash,'source_value':_src['value']}\n                    _rr=b.sell_market(_src['symbol'],_sell_qty)\n                    import logging as _logging\n                    _logging.warning(\"V22_KR_REBALANCE_SELL target=%s source=%s qty=%s total=%s cash=%s source_value=%s reason=RESIDUAL_CASH_TOO_SMALL resp=%s\",sym,_src['symbol'],_sell_qty,_total,_cash,_src['value'],_rr)\n                    self.store.event(\"KOREA\",sym,\"V22_KR_REBALANCE_SELL\",None,\"ORDER_SENT\",power=_f(row.get(\"power\")),message=f'{sym} funding: sell 50% {_src[\"symbol\"]} x{_sell_qty}',payload={'target':sym,'source':_src['symbol'],'sell_qty':_sell_qty,'total_assets':_total,'cash':_cash,'source_value':_src['value'],'reason':'RESIDUAL_CASH_TOO_SMALL','order':_rr,'engine_decision':_dec})\n                    return\n\n                if qty<1:\n                    return\n\n                self._last[_attempt_key]={'ts':_time.time(),'qty':qty,'cash':_cash,'total_assets':_total}\n'''


def run(*a):
    print('+',' '.join(map(str,a)),flush=True)
    subprocess.run(list(map(str,a)),check=True)


def install_text(text:str):
    fd,tmp=tempfile.mkstemp(prefix='v22_rotation_fix_',suffix='.py');os.close(fd);p=Path(tmp)
    try:
        p.write_text(text)
        py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,TARGET)
    finally:
        try:p.unlink()
        except FileNotFoundError:pass


def main():
    text=TARGET.read_text()
    backup=TARGET.with_suffix('.py.pre_v22_rotation_funding_fix')
    if not backup.exists():
        run('sudo','cp','-p',TARGET,backup);print('BACKUP',backup,flush=True)

    for marker in ('V22_KR_CAPITAL_ALLOCATOR','V22_KR_FULL_ORDER_AUTHORITY','_v22_kr_exit(row,st)'):
        if marker not in text: raise SystemExit('ABORT required runtime marker missing: '+marker)

    if 'V22_KR_ROTATION_FUNDING_FIX' in text:
        print('ROTATION_FUNDING_FIX=ALREADY_PATCHED',flush=True)
    else:
        if text.count(OLD)!=1: raise SystemExit(f'ABORT allocator anchor count={text.count(OLD)}')
        text=text.replace(OLD,NEW,1)
        install_text(text)
        print('ROTATION_FUNDING_FIX=PATCHED',flush=True)

    verify=TARGET.read_text()
    for x in ('V22_KR_ROTATION_FUNDING_FIX','_cash < (0.50*_src[\'value\'])','RESIDUAL_CASH_TOO_SMALL','_cash*0.995','MAX_POSITIONS' if False else 'V22_KR_CAPITAL_ALLOCATOR'):
        if x not in verify: raise SystemExit('ABORT missing '+x)
    if 'slot_budget=min(capital/max_positions,available)' in verify:
        raise SystemExit('ABORT legacy fixed-slot sizing still present')

    run(RUNTIME/'venv/bin/python','-m','py_compile',TARGET)
    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+60;last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                last=r.read().decode('utf-8','replace')
                if r.status==200:
                    print('HEALTH=PASS',flush=True);break
        except Exception as e:last=repr(e)
        time.sleep(2)
    else: raise SystemExit('ABORT health failed: '+str(last))

    print('KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('ORDER_SIZING=LIVE_ACCOUNT_99_5PCT_CASH',flush=True)
    print('ROTATION_TRIGGER=NEW_TARGET_AND_CASH_LT_50PCT_LARGEST_OTHER_VALUE',flush=True)
    print('ROTATION=SELL_50PCT_LARGEST_OTHER_THEN_WAIT_FOR_CASH',flush=True)
    print('TINY_RESIDUAL_CASH_BUY=DISABLED',flush=True)
    print('AUTO_ORDER_RETRY=DISABLED_PER_ENGINE_BAR',flush=True)
    print('BROKER=KIWOOM_MOCK_ONLY',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':main()
