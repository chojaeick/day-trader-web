from __future__ import annotations

"""Patch KR Kiwoom MOCK V22 order sizing to use live account capital.

Policy:
- Read live kt00004 immediately before every BUY decision.
- With available cash, deploy ~99.5% of cash (whole shares), never fixed 1-share sizing.
- If cash cannot buy one share and another holding owns the capital, submit ONE 50%
  rebalance SELL of the largest other holding and wait for a later refresh/account read
  before any BUY. Never chain BUY before the SELL has actually released cash.
- Up to 4 simultaneous holdings.
- No automatic retry loop for BUY/SELL submissions. Per-bar attempt keys make order
  submission idempotent when the same engine decision is rendered repeatedly.
- Kiwoom MOCK only; broker endpoint safety remains in KiwoomMockBroker.
"""

from pathlib import Path
import os
import py_compile
import subprocess
import tempfile
import time
import urllib.request

RUNTIME = Path('/home/ubuntu/day-trader-api')
SERVICE = 'day-trader-api'
TARGET = RUNTIME / 'live_server/v4_engine.py'

OLD = '''                # Retry guard: avoid hammering Kiwoom if a pending breakout survives multiple refreshes.\n                import time as _time\n                retry_key=("WILLIAMS_MOCK_RETRY",sym)\n                last_try=self._last.get(retry_key) or {}\n                if (_time.time()-_f(last_try.get("ts"),0)) < 15.0:\n                    return\n                self._last[retry_key]={"ts":_time.time()}\n                import time as _time\n                capital=float(os.getenv("WILLIAMS_MOCK_CAPITAL_KRW","1000000") or 1000000)\n                max_positions=max(1,int(os.getenv("WILLIAMS_MOCK_MAX_POSITIONS","5") or 5))\n                price=_f(row.get("price"))\n                if price<=0:\n                    return\n'''

NEW = '''                # V22_KR_CAPITAL_ALLOCATOR: live-account sizing, compounding, no fixed qty.\n                import time as _time\n                price=_f(row.get("price"))\n                if price<=0:\n                    return\n\n                # One broker submission per symbol/engine bar. A failed order is NOT\n                # automatically retried; a later distinct V22 bar may create a new decision.\n                _dec=row.get('engine5_v22_decision') or {}\n                _bar=str(_dec.get('bar_time') or row.get('updated_at') or '')\n                _attempt_key=("V22_KR_ORDER_ATTEMPT",sym,_bar)\n                if self._last.get(_attempt_key):\n                    return\n\n                # Read broker account immediately before sizing. Never size from stale UI\n                # state or the old WILLIAMS_MOCK_CAPITAL_KRW constant.\n                _bal=b.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})\n                def _n(v):\n                    try:return float(str(v or '0').replace(',','').replace('+','').strip() or 0)\n                    except Exception:return 0.0\n                _cash=max(0.0,_n(_bal.get('entr') or _bal.get('dnca_tot_amt') or _bal.get('deposit') or _bal.get('cash')))\n                _total=max(0.0,_n(_bal.get('tot_evlt_amt') or _bal.get('tot_est_amt') or _bal.get('estimated_assets') or _bal.get('tot_assets')))\n                _holds=[]\n                for _x in (_bal.get('stk_acnt_evlt_prst') or _bal.get('acnt_evlt_prst') or []):\n                    _hs=str(_x.get('stk_cd') or _x.get('stk_no') or _x.get('code') or '').replace('A','').zfill(6)\n                    try:_hq=int(_n(_x.get('rmnd_qty') or _x.get('hldg_qty') or _x.get('hold_qty') or _x.get('qty')))\n                    except Exception:_hq=0\n                    _hp=abs(_n(_x.get('cur_prc') or _x.get('now_prc') or _x.get('prpr') or _x.get('avg_prc')))\n                    _hv=abs(_n(_x.get('evlt_amt') or _x.get('evlt_prst') or _x.get('cur_amt')))\n                    if _hv<=0 and _hp>0:_hv=_hp*_hq\n                    if _hs and _hq>0:_holds.append({'symbol':_hs,'qty':_hq,'price':_hp,'value':_hv})\n                if _total<=0:\n                    _total=_cash+sum(_h['value'] for _h in _holds)\n\n                _max_positions=4\n                _held_syms={_h['symbol'] for _h in _holds}\n                if sym not in _held_syms and len(_held_syms)>=_max_positions:\n                    return\n\n                # Use essentially all currently free cash. 0.5% reserve absorbs\n                # quote movement/fees while preserving full-capital compounding.\n                _budget=max(0.0,_cash*0.995)\n                qty=int(_budget//price)\n\n                if qty<1:\n                    # Capital is invested. Rotate once by selling ~50% of the largest\n                    # OTHER position, then wait for a later account read before BUY.\n                    _others=[_h for _h in _holds if _h['symbol']!=sym and _h['qty']>0]\n                    if not _others:\n                        return\n                    _src=max(_others,key=lambda _h:_h['value'])\n                    _sell_qty=max(1,int(_src['qty']*0.50))\n                    _reb_key=("V22_KR_REBALANCE_ATTEMPT",sym,_bar,_src['symbol'])\n                    if self._last.get(_reb_key):\n                        return\n                    self._last[_reb_key]={'ts':_time.time(),'qty':_sell_qty}\n                    _rr=b.sell_market(_src['symbol'],_sell_qty)\n                    import logging as _logging\n                    _logging.warning("V22_KR_REBALANCE_SELL target=%s source=%s qty=%s total=%s cash=%s resp=%s",sym,_src['symbol'],_sell_qty,_total,_cash,_rr)\n                    self.store.event("KOREA",sym,"V22_KR_REBALANCE_SELL",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} funding: sell 50% {_src["symbol"]} x{_sell_qty}',payload={'target':sym,'source':_src['symbol'],'sell_qty':_sell_qty,'total_assets':_total,'cash':_cash,'order':_rr,'engine_decision':_dec})\n                    return\n\n                self._last[_attempt_key]={'ts':_time.time(),'qty':qty,'cash':_cash,'total_assets':_total}\n                row['v22_capital_allocation']={'total_assets':_total,'cash':_cash,'budget':_budget,'qty':qty,'price':price,'invest_pct_of_cash':round((qty*price/_cash*100),2) if _cash else 0,'max_positions':_max_positions}\n'''

OLD_ALLOC = '''                # Reserve capital for positions opened by this bridge in the current process.\n                reserved=0.0\n                open_count=0\n                for _k,_st in list(self._last.items()):\n                    if not (isinstance(_k,tuple) and len(_k)>=2 and _k[0]=="WILLIAMS_MOCK"):\n                        continue\n                    if not isinstance(_st,dict) or not _st.get("in_pos"):\n                        continue\n                    open_count+=1\n                    reserved += _f(_st.get("entry_price"))*_f(_st.get("qty"),1)\n                if open_count>=max_positions:\n                    return\n                available=max(0.0,capital-reserved)\n                if available < price:\n                    return\n                slot_budget=min(capital/max_positions,available)\n                qty=int(slot_budget//price)\n                if qty<1:\n                    qty=1\n                if qty*price>available:\n                    return\n\n'''


def run(*args):
    print('+',' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True)


def install_text(text: str):
    fd, tmp = tempfile.mkstemp(prefix='v22_allocator_', suffix='.py')
    os.close(fd)
    p=Path(tmp)
    try:
        p.write_text(text)
        py_compile.compile(str(p),doraise=True)
        run('sudo','install','-m','0644',p,TARGET)
    finally:
        try:p.unlink()
        except FileNotFoundError:pass


def main():
    text=TARGET.read_text()
    backup=TARGET.with_suffix('.py.pre_v22_capital_allocator')
    if not backup.exists():
        run('sudo','cp','-p',TARGET,backup)
        print('BACKUP',backup,flush=True)

    if 'V22_KR_CAPITAL_ALLOCATOR' in text:
        print('CAPITAL_ALLOCATOR=ALREADY_PATCHED',flush=True)
    else:
        if text.count(OLD)!=1:
            raise SystemExit(f'ABORT sizing anchor count={text.count(OLD)}')
        if text.count(OLD_ALLOC)!=1:
            raise SystemExit(f'ABORT legacy allocation anchor count={text.count(OLD_ALLOC)}')
        text=text.replace(OLD,NEW,1).replace(OLD_ALLOC,'',1)
        # Runtime is root-owned: compile a temp file, then atomically install with sudo.
        install_text(text)
        print('CAPITAL_ALLOCATOR=PATCHED',flush=True)

    verify=TARGET.read_text()
    required=['V22_KR_CAPITAL_ALLOCATOR','_cash*0.995','qty=int(_budget//price)','V22_KR_REBALANCE_SELL']
    for x in required:
        if x not in verify:raise SystemExit('ABORT missing '+x)
    if 'slot_budget=min(capital/max_positions,available)' in verify:
        raise SystemExit('ABORT legacy fixed-slot sizing still present')
    # Ensure the full V22 BUY+SELL authority patch was not overwritten.
    if 'V22_KR_FULL_ORDER_AUTHORITY' not in verify or '_v22_kr_exit(row,st)' not in verify:
        raise SystemExit('ABORT V22 full exit authority missing after allocator patch')

    run(RUNTIME/'venv/bin/python','-m','py_compile',TARGET)
    run('sudo','systemctl','restart',SERVICE)
    deadline=time.time()+60; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
                last=r.read().decode('utf-8','replace')
                if r.status==200:
                    print('HEALTH=PASS',flush=True); break
        except Exception as e:last=repr(e)
        time.sleep(2)
    else:raise SystemExit(f'ABORT health failed: {last}')

    print('KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('ORDER_SIZING=LIVE_ACCOUNT_99_5PCT_CASH',flush=True)
    print('MAX_POSITIONS=4',flush=True)
    print('ROTATION=SELL_50PCT_LARGEST_OTHER_THEN_WAIT_FOR_CASH',flush=True)
    print('AUTO_ORDER_RETRY=DISABLED_PER_ENGINE_BAR',flush=True)
    print('BROKER=KIWOOM_MOCK_ONLY',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':main()
