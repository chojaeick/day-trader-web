#!/usr/bin/env python3
from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
s=p.read_text()

if 'KIWOOM MOCK AUTO V102' in s:
    print('ALREADY_PATCHED')
    raise SystemExit(0)

# import broker
anchor='import os\n'
if anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: import os')
s=s.replace(anchor, anchor+'from live_server.kiwoom_mock_broker import KiwoomMockBroker\n',1)

# init broker after class engine init body acquires store/db; use first explicit runtime mode init as stable nearby target
init_anchor='self._runtime_mode = "NORMAL"'
if init_anchor not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: runtime_mode init')
init_block='''self._runtime_mode = "NORMAL"\n        # === KIWOOM MOCK AUTO V102 ===\n        self._kiwoom_mock_broker = None\n        self._kiwoom_mock_last_order_ts = 0.0\n        self._kiwoom_mock_auto_enabled = os.getenv("KIWOOM_MOCK_AUTO_ENABLED", "0").lower() in ("1","true","yes","on")\n        if self._kiwoom_mock_auto_enabled:\n            try:\n                self._kiwoom_mock_broker = KiwoomMockBroker()\n                if not self._kiwoom_mock_broker.cfg.order_enable:\n                    self._kiwoom_mock_auto_enabled = False\n            except Exception:\n                self._kiwoom_mock_broker = None\n                self._kiwoom_mock_auto_enabled = False\n'''
s=s.replace(init_anchor,init_block,1)

# add helper before finalize
helper_anchor='    def _finalize('
idx=s.find(helper_anchor)
if idx<0:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: _finalize')
helper='''    # === KIWOOM MOCK AUTO V102 ===\n    def _kiwoom_mock_williams_step(self, market, row):\n        if str(market).upper() != "KOREA":\n            return row\n        row["kiwoom_mock_auto"] = bool(self._kiwoom_mock_auto_enabled)\n        row["kiwoom_mock_order"] = None\n        if not self._kiwoom_mock_auto_enabled or self._kiwoom_mock_broker is None:\n            return row\n        try:\n            import time\n            now=time.time()\n            if now - float(self._kiwoom_mock_last_order_ts or 0) < 1.1:\n                return row\n            sym=str(row.get("symbol") or row.get("code") or "").replace("A","").zfill(6)\n            # Existing frozen Williams flags only. No new signal logic here.\n            entry=bool(row.get("williams_entry") or row.get("williams_signal_entry"))\n            exit_ready=bool(row.get("williams_exit_ready"))\n            if entry:\n                # V102 safety: one-share only until live engine bridge is observed in-session.\n                r=self._kiwoom_mock_broker.buy_market(sym,1)\n                self._kiwoom_mock_last_order_ts=now\n                row["kiwoom_mock_order"]={"side":"BUY","qty":1,"ord_no":r.get("ord_no"),"return_code":r.get("return_code")}\n            elif exit_ready:\n                # V102 safety: one-share only. Broker/account reconciliation comes next.\n                r=self._kiwoom_mock_broker.sell_market(sym,1)\n                self._kiwoom_mock_last_order_ts=now\n                row["kiwoom_mock_order"]={"side":"SELL","qty":1,"ord_no":r.get("ord_no"),"return_code":r.get("return_code")}\n        except Exception as e:\n            row["kiwoom_mock_order_error"]=str(e)[:300]\n        return row\n\n'''
s=s[:idx]+helper+s[idx:]

# wire after paper step if present
wire='self._paper_williams_step(market,r)'
if wire not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: paper step wire')
s=s.replace(wire,wire+'\n        self._kiwoom_mock_williams_step(market,r)',1)

p.write_text(s)
print('PATCHED',p)
print('ADDED=KIWOOM MOCK AUTO V102')
print('DEFAULT_AUTO=OFF')
print('REQUIRES=KIWOOM_MOCK_AUTO_ENABLED=1 + KIWOOM_MOCK_ORDER_ENABLE=1')
print('SAFETY=KOREA_ONLY,ONE_SHARE,MOCK_BROKER_ONLY,1.1S_THROTTLE')
