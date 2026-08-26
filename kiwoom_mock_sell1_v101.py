#!/usr/bin/env python3
import os, time, json
from live_server.kiwoom_mock_broker import KiwoomMockBroker

SYMBOL=os.getenv('KIWOOM_MOCK_TEST_SYMBOL','005930').strip() or '005930'
QTY=int(os.getenv('KIWOOM_MOCK_TEST_QTY','1'))

print('=== KIWOOM MOCK SELL1 V101 ===')
print('SYMBOL',SYMBOL,'QTY',QTY)

b=KiwoomMockBroker()
acct=b.account_number()
print('ACCOUNT', acct[:4]+'****'+acct[-2:])
print('ORDER_ENABLE', b.cfg.order_enable)
if not b.cfg.order_enable:
    raise SystemExit('ORDER_DISABLED: set KIWOOM_MOCK_ORDER_ENABLE=1')

time.sleep(1.2)
r=b.sell_market(SYMBOL,QTY)
print('ORDER_RETURN_CODE', r.get('return_code'))
print('ORDER_RETURN_MSG', r.get('return_msg'))
print('ORDER_NO', r.get('ord_no') or r.get('order_no') or r.get('ordNo'))
print('RAW_KEYS', sorted(r.keys()))
print('PASS: mock market SELL request accepted')
