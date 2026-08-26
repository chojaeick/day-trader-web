#!/usr/bin/env python3
import os, time
from live_server.kiwoom_mock_broker import KiwoomMockBroker

SYMBOL=os.getenv('KIWOOM_MOCK_TEST_SYMBOL','005930').strip() or '005930'
QTY=int(os.getenv('KIWOOM_MOCK_TEST_QTY','1'))

print('=== KIWOOM MOCK BUY1 V99B ===')
print('SYMBOL',SYMBOL,'QTY',QTY)

b=KiwoomMockBroker()
acct=b.account_number()
print('ACCOUNT', acct[:4]+'****'+acct[-2:])
print('ORDER_ENABLE', b.cfg.order_enable)
if not b.cfg.order_enable:
    raise SystemExit('ORDER_DISABLED: set KIWOOM_MOCK_ORDER_ENABLE=1 for this one mock-order test')

# Kiwoom mock account TRs are rate-limited; avoid back-to-back account/order calls.
time.sleep(1.2)
r=b.buy_market(SYMBOL,QTY)
print('ORDER_RETURN_CODE', r.get('return_code'))
print('ORDER_RETURN_MSG', r.get('return_msg'))
print('ORDER_NO', r.get('ord_no') or r.get('order_no') or r.get('ordNo'))
print('RAW_KEYS', sorted(r.keys()))
print('PASS: mock market BUY request accepted')
