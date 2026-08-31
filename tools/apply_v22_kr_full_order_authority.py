from __future__ import annotations

"""Deploy full KR Engine5 V22 BUY+SELL authority to Kiwoom MOCK runtime.

Removes Williams STRUCT0 exit and legacy -1.5% emergency sell from the KR broker
order path. Engine5 V22 alone decides broker exits. Williams remains telemetry.
Runtime files may be root-owned, so installs/writes use sudo-safe temp files.
"""

from pathlib import Path
import py_compile, shutil, subprocess, tempfile, time, urllib.request

REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNTIME=Path('/home/ubuntu/day-trader-api')
TARGET=RUNTIME/'live_server/v4_engine.py'
SERVICE='day-trader-api'

OLD_EXIT='''            elif in_pos:\n                import time as _time\n                qty=max(1,int(_f(st.get("qty"),1)))\n                entry_price=_f(st.get("entry_price"))\n                price=_f(row.get("price"))\n                entered_ts=_f(st.get("entered_ts"))\n                hold_sec=(_time.time()-entered_ts) if entered_ts else 999999.0\n                hard_stop=bool(entry_price and price and price<=entry_price*0.985)\n\n                # V118: row EXIT_READY is now computed from bars strictly after\n                # this position's BUY minute. Pre-entry support cannot trigger this exit.\n                # Emergency -1.5% hard stop remains independent and immediate.\n                if not hard_stop:\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n                sell_order_no=r.get("ord_no") or r.get("order_no")\n                self._last[key]={"in_pos":False,"sell_order_no":sell_order_no,"qty":qty,"entry_price":entry_price,"entered_ts":entered_ts}\n                import logging as _logging\n                _logging.warning("WILLIAMS_MOCK_SELL_ACCEPTED sym=%s qty=%s price=%s hold_sec=%.1f hard_stop=%s order_no=%s",sym,qty,price,hold_sec,hard_stop,sell_order_no)\n                self.store.event("KOREA",sym,"WILLIAMS_MOCK_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock SELL {qty}',payload={"order":r,"row":row,"qty":qty,"entry_price":entry_price,"hold_sec":hold_sec,"hard_stop":hard_stop})\n'''

NEW_EXIT='''            elif in_pos:\n                # V22_KR_FULL_ORDER_AUTHORITY: Williams/STRUCT0 and the legacy\n                # -1.5% emergency stop have ZERO broker SELL authority.\n                from live_server.engine5_v22_live_kr import evaluate_exit as _v22_kr_exit\n                import time as _time\n                qty=max(1,int(_f(st.get("qty"),1)))\n                entry_price=_f(st.get("entry_price"))\n                price=_f(row.get("price"))\n                entered_ts=_f(st.get("entered_ts"))\n                _exit=_v22_kr_exit(row,st)\n                row['engine5_v22_exit_decision']=_exit\n                if not bool(_exit.get('exit')):\n                    return\n                sell_qty=min(qty,max(1,int(_f(_exit.get('sell_qty'),qty))))\n                _bar=str((row.get('engine5_v22_decision') or {}).get('bar_time') or row.get('updated_at') or '')\n                _reason=str(_exit.get('reason') or 'V22_EXIT')\n                _attempt=("V22_KR_EXIT_ATTEMPT",sym,_bar,_reason)\n                if self._last.get(_attempt):\n                    return\n                self._last[_attempt]={'ts':_time.time(),'qty':sell_qty}\n                r=b.sell_market(sym,sell_qty)\n                sell_order_no=r.get("ord_no") or r.get("order_no")\n                remain=max(0,qty-sell_qty)\n                if remain>0:\n                    st=dict(st)\n                    st['in_pos']=True\n                    st['qty']=remain\n                    st['sell_order_no']=sell_order_no\n                    if bool(_exit.get('tp1_done')):\n                        st['v22_tp1_done']=True\n                        st['tp1_done']=True\n                    if bool(_exit.get('outer_reduced')):\n                        st['v22_outer_reduced']=True\n                    self._last[key]=st\n                else:\n                    self._last[key]={"in_pos":False,"sell_order_no":sell_order_no,"qty":0,"entry_price":entry_price,"entered_ts":entered_ts,"engine":"ENGINE5_V22_KR_LIVE"}\n                import logging as _logging\n                _logging.warning("V22_KR_SELL_ACCEPTED sym=%s qty=%s remain=%s price=%s reason=%s order_no=%s",sym,sell_qty,remain,price,_reason,sell_order_no)\n                self.store.event("KOREA",sym,"V22_KR_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} V22 SELL {sell_qty} {_reason}',payload={"order":r,"row":row,"sell_qty":sell_qty,"remain":remain,"entry_price":entry_price,"exit_decision":_exit})\n'''


def run(*a):
    print('+',' '.join(map(str,a)),flush=True); subprocess.run(list(map(str,a)),check=True)


def sudo_install(src: Path, dst: Path):
    run('sudo','install','-m','0644',str(src),str(dst))


def sudo_write_text(dst: Path, text: str):
    with tempfile.NamedTemporaryFile('w',delete=False) as f:
        f.write(text); tmp=Path(f.name)
    try:
        run('sudo','install','-m','0644',str(tmp),str(dst))
    finally:
        tmp.unlink(missing_ok=True)


def main():
    src=REPO/'live_server/engine5_v22_live_kr.py'; dst=RUNTIME/'live_server/engine5_v22_live_kr.py'
    py_compile.compile(str(src),doraise=True)
    sudo_install(src,dst)
    print('INSTALLED live_server/engine5_v22_live_kr.py',flush=True)

    text=TARGET.read_text()
    backup=TARGET.with_suffix('.py.pre_v22_full_order_authority')
    if not backup.exists():
        run('sudo','cp','-p',str(TARGET),str(backup)); print('BACKUP',backup,flush=True)
    if 'V22_KR_FULL_ORDER_AUTHORITY' not in text:
        if text.count(OLD_EXIT)!=1: raise SystemExit(f'ABORT exit anchor count={text.count(OLD_EXIT)}')
        text=text.replace(OLD_EXIT,NEW_EXIT,1)
        print('V22_EXIT_AUTHORITY=PATCHED',flush=True)
    else: print('V22_EXIT_AUTHORITY=ALREADY_PATCHED',flush=True)

    marker='                    "tp1_done":False,\n'
    replacement='                    "tp1_done":False,\n                    "original_qty":qty,\n                    "v22_tp1_done":False,\n                    "v22_outer_reduced":False,\n'
    if '"original_qty":qty' not in text:
        if text.count(marker)!=1: raise SystemExit(f'ABORT position-state anchor count={text.count(marker)}')
        text=text.replace(marker,replacement,1)
        print('V22_RUNNER_STATE=PATCHED',flush=True)

    with tempfile.NamedTemporaryFile('w',suffix='.py',delete=False) as f:
        f.write(text); tmp=Path(f.name)
    try:
        py_compile.compile(str(tmp),doraise=True)
    finally:
        tmp.unlink(missing_ok=True)
    sudo_write_text(TARGET,text)

    verify=TARGET.read_text()
    banned=['hard_stop=bool(entry_price and price and price<=entry_price*0.985)','if not exit_ready:','WILLIAMS_MOCK_SELL_ACCEPTED']
    a=verify.index('    def _williams_mock_auto_step(self, row):'); b=verify.index('    def _finalize(self,market,rows):',a); block=verify[a:b]
    for x in banned:
        if x in block: raise SystemExit('ABORT legacy KR broker SELL authority remains: '+x)
    for x in ['V22_KR_FULL_ORDER_AUTHORITY','_v22_kr_exit(row,st)','V22_KR_SELL_ACCEPTED']:
        if x not in block: raise SystemExit('ABORT missing '+x)

    run(RUNTIME/'venv/bin/python','-c',"from live_server.engine5_v22_live_kr import ENGINE_NAME,evaluate_entry,evaluate_exit; assert ENGINE_NAME=='ENGINE5_V22_KR_LIVE'; print('V22_EXIT_IMPORT=PASS')")
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
    print('KR_BUY_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('KR_SELL_AUTHORITY=ENGINE5_V22_KR_LIVE',flush=True)
    print('WILLIAMS_BROKER_EXIT_AUTHORITY=DISABLED',flush=True)
    print('LEGACY_MINUS_1_5PCT_EXIT=DISABLED',flush=True)
    print('V22_EXIT=STOP_-1R__TP1_+2R_50PCT__RUNNER',flush=True)
    print('BROKER=KIWOOM_MOCK_ONLY',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__': main()
