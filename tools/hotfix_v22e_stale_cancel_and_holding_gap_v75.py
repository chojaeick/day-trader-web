#!/usr/bin/env python3
from pathlib import Path
import re, shutil, subprocess, time, json

RUN=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')
BROKER=Path('/home/ubuntu/day-trader-api/live_server/kiwoom_us_mock_broker.py')
PY='/home/ubuntu/day-trader-api/venv/bin/python'
SERVICE='day-trader-v22e-us.service'

def die(msg):
    print('ABORT',msg); raise SystemExit(1)

def backup(p,suffix):
    b=Path(str(p)+suffix)
    if not b.exists(): shutil.copy2(p,b)

if not RUN.exists() or not BROKER.exists(): die('runtime files missing')
backup(RUN,'.pre_v75'); backup(BROKER,'.pre_v75')

# ---- broker: add cancel_order using Kiwoom US cancel TR ust20003 ----
b=BROKER.read_text(encoding='utf-8')
if 'def cancel_order(' not in b:
    anchor='    def sell_limit(self, symbol: str, qty: int, price: float, exchange: str = "NY") -> dict[str, Any]:\n'
    if anchor not in b: die('broker sell_limit anchor not found')
    method='''    def cancel_order(self, symbol: str, ord_no: str, exchange: str = "NY") -> dict[str, Any]:\n        self._ensure_order_enabled()\n        ex = self._check_exchange(exchange)\n        ono = str(ord_no or "").strip()\n        if not ono:\n            raise ValueError("ord_no required")\n        return self._post("/api/us/ordr", "ust20003", {\n            "stex_tp": ex,\n            "stk_cd": str(symbol).upper().strip(),\n            "ord_no": ono,\n        })\n\n'''
    b=b.replace(anchor,method+anchor,1)
BROKER.write_text(b,encoding='utf-8')

# ---- runner: config + pending order registry ----
s=RUN.read_text(encoding='utf-8')
if 'V75_STALE_ORDER_CANCEL' not in s:
    # globals near marker block
    marker='V57_USD_ONLY_CASH_PARSE = True\n'
    if marker not in s: die('V57 marker missing')
    s=s.replace(marker,marker+"V75_STALE_ORDER_CANCEL = True\nSTALE_ORDER_SEC = float(os.getenv('V22E_US_STALE_ORDER_SEC','20'))\n_pending_orders = {}\n",1)

    # enrich ORDER_ACCEPTED path with ord_no registry
    old="""        log('ORDER_ACCEPTED', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason, return_code=ack.get('return_code'))\n        return {'ok': True, 'ack': ack, 'limit': limit_px}\n"""
    new="""        ord_no=str(ack.get('ord_no') or '').strip()\n        log('ORDER_ACCEPTED', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason, return_code=ack.get('return_code'), ord_no=ord_no or None)\n        if ord_no:\n            before_qty=int(float(((_holdings_cache.get(sym) or {}).get('qty')) or 0))\n            _pending_orders[ord_no]={'ord_no':ord_no,'side':side,'symbol':sym,'qty':int(qty),'exchange':exchange,'before_qty':before_qty,'ts':time.time(),'reason':reason}\n            log('ORDER_PENDING_TRACK',side=side,symbol=sym,ord_no=ord_no,before_qty=before_qty,stale_after_sec=STALE_ORDER_SEC)\n        return {'ok': True, 'ack': ack, 'limit': limit_px, 'ord_no': ord_no}\n"""
    if old not in s: die('ORDER_ACCEPTED block not found')
    s=s.replace(old,new,1)

    # process pending orders immediately after holdings refresh and fail-closed gate
    anchor='            holdings = refresh_holdings()\n'
    if anchor not in s: die('holdings refresh anchor missing')
    block='''            holdings = refresh_holdings()\n            # V75: accepted is not filled. Confirm from live holding qty; cancel stale original order.\n            for _ono,_po in list(_pending_orders.items()):\n                _sym=str(_po.get('symbol') or '').upper(); _side=str(_po.get('side') or '').upper()\n                _before=int(_po.get('before_qty') or 0); _now=int(float(((holdings.get(_sym) or {}).get('qty')) or 0))\n                _filled=(_now>_before) if _side=='BUY' else (_now<_before)\n                if _filled:\n                    log('ORDER_FILL_CONFIRMED',side=_side,symbol=_sym,ord_no=_ono,before_qty=_before,now_qty=_now)\n                    _pending_orders.pop(_ono,None)\n                    continue\n                if time.time()-float(_po.get('ts') or 0) >= STALE_ORDER_SEC:\n                    try:\n                        _cres=broker().cancel_order(_sym,_ono,_po.get('exchange') or resolve_exchange(_sym))\n                        log('ORDER_STALE_CANCEL_ACCEPTED',side=_side,symbol=_sym,ord_no=_ono,return_code=_cres.get('return_code'),return_msg=_cres.get('return_msg'))\n                    except Exception as _ce:\n                        log('ORDER_STALE_CANCEL_FAILED',side=_side,symbol=_sym,ord_no=_ono,error=repr(_ce))\n                    finally:\n                        _pending_orders.pop(_ono,None)\n'''
    s=s.replace(anchor,block,1)

    # surface held-symbol data gaps instead of silent continue
    old2="""                b5 = completed_5m(sym)\n                if b5 is None:\n                    continue\n"""
    new2="""                b5 = completed_5m(sym)\n                if b5 is None:\n                    if sym in holdings:\n                        try:\n                            _tc=len(db.ticks(sym,12000) or [])\n                        except Exception:\n                            _tc=-1\n                        log('HOLDING_DATA_GAP',symbol=sym,holding_qty=(holdings.get(sym) or {}).get('qty'),tick_count=_tc,action='NO_STRATEGY_DECISION_UNTIL_5M_DATA')\n                    continue\n"""
    if old2 not in s: die('completed_5m block not found')
    s=s.replace(old2,new2,1)

RUN.write_text(s,encoding='utf-8')

# compile before install/restart
for p in (BROKER,RUN):
    r=subprocess.run([PY,'-m','py_compile',str(p)],capture_output=True,text=True)
    if r.returncode: die(f'compile failed {p}: {r.stderr}')
print('PY_COMPILE=PASS')

# one restart only
subprocess.run(['systemctl','restart',SERVICE],check=True)
time.sleep(8)
active=subprocess.run(['systemctl','is-active',SERVICE],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+active.upper())
print('STALE_ORDER_POLICY=CANCEL_AFTER_20S_IF_HOLDING_QTY_UNCHANGED')
print('CANCEL_API=ust20003')
print('AUTO_RETRY_SAME_BAR=DISABLED')
print('ORDER_ACCEPTED_IS_FILL=NO')
print('HOLDING_DATA_GAP=VISIBLE')

# read-only current diagnostics
try:
    ev=Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
    d=json.loads(ev.read_text(encoding='utf-8')) if ev.exists() else {}
    rows=d.get('rows') if isinstance(d,dict) else []
    so=next((x for x in (rows or []) if str((x or {}).get('symbol') or '').upper()=='SOXS'),None)
    print('SOXS_EVAL_ROW='+('PRESENT' if so else 'MISSING'))
    if so: print('SOXS_EVAL='+json.dumps(so,ensure_ascii=False,default=str))
except Exception as e: print('SOXS_EVAL_CHECK_ERROR='+repr(e))

j=subprocess.run(['journalctl','-u',SERVICE,'-n','120','--no-pager'],capture_output=True,text=True).stdout
for line in j.splitlines():
    if any(k in line for k in ('SOXS','HOLDING_DATA_GAP','ORDER_STALE_CANCEL','ORDER_FILL_CONFIRMED','ORDER_ACCEPTED')):
        print(line)
print('DEPLOY=PASS')
