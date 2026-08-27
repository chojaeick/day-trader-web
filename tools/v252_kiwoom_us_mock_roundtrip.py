#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv('/home/ubuntu/day-trader-api/.env', override=False)

from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker


def dump(label, obj):
    print(label, json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def latest_tick_price(db_path: str, symbol: str) -> float:
    with sqlite3.connect(db_path) as c:
        row = c.execute(
            "SELECT price,ts FROM ticks WHERE symbol=? ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"no live tick for {symbol}")
    price = float(row[0])
    print(f"LIVE_TICK_PRICE={price} TS={row[1]}", flush=True)
    return price


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbol', default='SOXL', choices=['SOXL','SOXS'])
    ap.add_argument('--exchange', default='NY', choices=['NY','ND','NA'])
    ap.add_argument('--qty', type=int, default=1)
    ap.add_argument('--wait-sec', type=float, default=5.0)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--db', default='/home/ubuntu/day-trader-api/daytrader.db')
    ap.add_argument('--cross-pct', type=float, default=0.01,
                    help='marketable limit offset: buy above live price, sell below live price')
    args = ap.parse_args()

    print('=== V252B KIWOOM US MOCK LIMIT ROUNDTRIP ===', flush=True)
    print(f'SYMBOL={args.symbol} EXCHANGE={args.exchange} QTY={args.qty} MOCK_ONLY=YES LIMIT_ONLY=YES', flush=True)
    print('REAL_ORDER_PATH=BLOCKED BY ADAPTER', flush=True)

    broker = KiwoomUSMockBroker()
    token = broker.get_token()
    print('TOKEN_OK=YES TOKEN_LEN=', len(token), flush=True)

    before = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_BEFORE=', before)

    live = latest_tick_price(args.db, args.symbol)
    buy_limit = round(live * (1.0 + max(0.001, args.cross_pct)), 4)
    sell_limit = round(live * (1.0 - max(0.001, args.cross_pct)), 4)
    print(f'BUY_LIMIT={buy_limit} SELL_LIMIT={sell_limit}', flush=True)

    if not args.execute:
        print('PREFLIGHT_PASS=YES', flush=True)
        print('NEXT=rerun with --execute for 1-share mock limit roundtrip', flush=True)
        return

    if os.getenv('KIWOOM_MOCK_US_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'):
        raise SystemExit('BLOCKED: export KIWOOM_MOCK_US_ORDER_ENABLE=1 first')

    buy = broker.buy_limit(args.symbol, args.qty, buy_limit, args.exchange)
    dump('BUY_ACK=', buy)
    time.sleep(max(1.0,args.wait_sec))

    after_buy = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_AFTER_BUY=', after_buy)

    live2 = latest_tick_price(args.db, args.symbol)
    sell_limit = round(live2 * (1.0 - max(0.001, args.cross_pct)), 4)
    print(f'SELL_LIMIT_REFRESHED={sell_limit}', flush=True)
    sell = broker.sell_limit(args.symbol, args.qty, sell_limit, args.exchange)
    dump('SELL_ACK=', sell)
    time.sleep(max(1.0,args.wait_sec))

    after_sell = broker.balance(args.symbol, args.exchange)
    dump('BALANCE_AFTER_SELL=', after_sell)
    print('ROUNDTRIP_REQUESTS_COMPLETE=YES', flush=True)


if __name__ == '__main__':
    main()
