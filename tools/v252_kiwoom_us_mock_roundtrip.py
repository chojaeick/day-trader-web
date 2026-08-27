#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker


def dump(label, obj):
    print(label, json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='SOXL', choices=['SOXL','SOXS'])
    ap.add_argument('--exchange', default='NY', choices=['NY','ND','NA'])
    ap.add_argument('--qty', type=int, default=1)
    ap.add_argument('--wait-sec', type=float, default=5.0)
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    print('=== V252 KIWOOM US MOCK ROUNDTRIP ===', flush=True)
    print(f'SYMBOL={args.symbol} EXCHANGE={args.exchange} QTY={args.qty} MOCK_ONLY=YES', flush=True)
    print('REAL_ORDER_PATH=BLOCKED BY ADAPTER', flush=True)

    broker = KiwoomUSMockBroker()
    token = broker.get_token()
    print('TOKEN_OK=YES TOKEN_LEN=', len(token), flush=True)

    before = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_BEFORE=', before)

    if not args.execute:
        print('PREFLIGHT_PASS=YES', flush=True)
        print('NEXT=rerun with --execute after confirming mock account response', flush=True)
        return

    if os.getenv('KIWOOM_MOCK_US_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'):
        raise SystemExit('BLOCKED: export KIWOOM_MOCK_US_ORDER_ENABLE=1 first')

    buy = broker.buy_market(args.symbol, args.qty, args.exchange)
    dump('BUY_ACK=', buy)
    time.sleep(max(1.0,args.wait_sec))

    after_buy = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_AFTER_BUY=', after_buy)

    sell = broker.sell_market(args.symbol, args.qty, args.exchange)
    dump('SELL_ACK=', sell)
    time.sleep(max(1.0,args.wait_sec))

    after_sell = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_AFTER_SELL=', after_sell)
    print('ROUNDTRIP_REQUESTS_COMPLETE=YES', flush=True)


if __name__ == '__main__':
    main()
