from __future__ import annotations

from live_server.kiwoom_mock_broker import KiwoomMockBroker

print("=== KIWOOM MOCK BROKER V98 SMOKE ===")
print("NO ORDER IS SENT BY THIS SCRIPT")

b = KiwoomMockBroker()
print("BASE", b.cfg.rest_base)
print("ORDER_ENABLE", b.cfg.order_enable)

token = b.get_token()
print("TOKEN", "ISSUED" if token else "FAILED")

acct = b.account_number()
print("ACCOUNT", acct[:4] + "****" + acct[-2:] if len(acct) >= 6 else "MASKED")
print("PASS: mock OAuth + account lookup")
