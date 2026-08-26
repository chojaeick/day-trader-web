#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()

if '_williams_mock_auto_step' not in s:
    marker='    def _finalize(self,market,rows):\n'
    if marker not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: _finalize')
    method='''    def _williams_mock_auto_step(self, row):\n        import os\n        if os.getenv("KIWOOM_MOCK_AUTO_ENABLED","0").lower() not in ("1","true","yes","on"):\n            return\n        if (row.get("session") or "") != "REGULAR":\n            return\n        sym=str(row.get("symbol") or "").zfill(6)\n        if not sym:\n            return\n        try:\n            from live_server.kiwoom_mock_broker import KiwoomMockBroker\n            b=KiwoomMockBroker()\n            if not b.cfg.order_enable:\n                return\n            key=("WILLIAMS_MOCK",sym)\n            st=self._last.get(key,{})\n            in_pos=bool(st.get("in_pos"))\n            entry=bool(row.get("williams_entry") or row.get("williams_signal_entry"))\n            exit_ready=bool(row.get("williams_exit_ready"))\n            if entry and not in_pos:\n                r=b.buy_market(sym,1)\n                self._last[key]={"in_pos":True,"buy_order_no":r.get("ord_no") or r.get("order_no")}\n                self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY",None,"ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock BUY 1',payload={"order":r,"row":row})\n            elif exit_ready and in_pos:\n                r=b.sell_market(sym,1)\n                self._last[key]={"in_pos":False,"sell_order_no":r.get("ord_no") or r.get("order_no")}\n                self.store.event("KOREA",sym,"WILLIAMS_MOCK_SELL","HOLD","ORDER_SENT",power=_f(row.get("power")),message=f'{sym} Williams mock SELL 1',payload={"order":r,"row":row})\n        except Exception as e:\n            self.store.event("KOREA",sym,"WILLIAMS_MOCK_ERROR",None,"ERROR",power=_f(row.get("power")),message=str(e),payload={"row":row})\n\n'''
    s=s.replace(marker,method+marker,1)

old="""            elif market=='KOREA' and r.get('session')=='REGULAR':\n                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))\n"""
new="""            elif market=='KOREA' and r.get('session')=='REGULAR':\n                self.store.update_validation_outcomes(market,sym,_f(r.get('price')))\n                self._williams_mock_auto_step(r)\n"""
if new not in s:
    if old not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: korea finalize branch')
    s=s.replace(old,new,1)

P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('ADDED=_williams_mock_auto_step')
print('WIRED=KOREA_FINALIZE_REGULAR')
print('DEFAULT_AUTO=OFF')
print('ORDER_QTY=1')
print('REAL_BROKER_FALLBACK=NO')
