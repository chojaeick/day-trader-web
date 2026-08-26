#!/usr/bin/env python3
from __future__ import annotations
import os, sys, time, json
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
if str(RUNTIME) not in sys.path:
    sys.path.insert(0,str(RUNTIME))

import requests
from live_server.kiwoom_mock_broker import MockBrokerConfig, KiwoomMockBroker

SYMBOL='AAPL'
EXCHANGE='ND'
QTY=1
TAG='V221_KIWOOM_USA_MOCK_SMOKE'

print('=== V221 KIWOOM USA MOCK REAL-ORDER SMOKE ===')
print('TARGET=KIWOOM_MOCK_ACCOUNT USA')
print('REAL_ACCOUNT_ALLOWED=NO')
print('SYMBOL=',SYMBOL,'EXCHANGE=',EXCHANGE,'QTY=',QTY)

cfg=MockBrokerConfig.from_env()
print('REST_BASE=',cfg.rest_base)
print('ORDER_ENABLE=',cfg.order_enable)
if 'mockapi.kiwoom.com' not in cfg.rest_base:
    raise SystemExit('ABORT_NON_MOCK_BASE')
if not cfg.order_enable:
    raise SystemExit('ABORT_KIWOOM_MOCK_ORDER_ENABLE_FALSE')

b=KiwoomMockBroker(cfg)
token=b.get_token()
print('TOKEN_OK=',bool(token))

BASE=cfg.rest_base.rstrip('/')
def post(path,api_id,body):
    h={'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id}
    r=requests.post(BASE+path,headers=h,json=body,timeout=15)
    print('HTTP',api_id,r.status_code)
    try:d=r.json()
    except Exception:d={'raw':r.text}
    print(api_id,'BODY=',json.dumps(d,ensure_ascii=False))
    r.raise_for_status()
    return d

def balance():
    d=post('/api/us/acnt','ust21070',{'stex_tp':EXCHANGE,'stk_cd':SYMBOL})
    rows=d.get('result_list') or []
    row=None
    for x in rows:
        if str(x.get('stk_cd') or '').upper()==SYMBOL:
            row=x; break
    def f(k):
        try:return float(str((row or {}).get(k) or '0').replace(',',''))
        except Exception:return 0.0
    return d,row,f('poss_qty'),f('sell_alowq')

# Safety: do not touch an existing AAPL mock holding.
print('--- PRECHECK BALANCE ---')
_,pre,pre_qty,pre_sell=balance()
print('PRE_POSITION=',pre,'PRE_QTY=',pre_qty,'PRE_SELLABLE=',pre_sell)
if pre_qty>0 or pre_sell>0:
    raise SystemExit('ABORT_EXISTING_AAPL_MOCK_POSITION')

time.sleep(1.2)
print('--- BUY 1 SHARE MARKET ---')
buy=post('/api/us/ordr','ust20000',{
    'stex_tp':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':'','trde_tp':'03'
})
buy_ord=str(buy.get('ord_no') or buy.get('order_no') or '').strip()
buy_ok=(buy.get('return_code') in (None,0) and bool(buy_ord))
print('BUY_ORDER_NO=',buy_ord,'BUY_ACCEPTED=',buy_ok)
if not buy_ok:
    raise SystemExit('BUY_ORDER_NOT_ACCEPTED')

# Confirm actual mock-account holding before sell.
held=False; held_row=None
for i in range(1,11):
    time.sleep(2.0)
    try:
        _,row,qty,sellable=balance()
        print('BUY_FILL_CHECK',i,'QTY=',qty,'SELLABLE=',sellable)
        if qty>=1 or sellable>=1:
            held=True; held_row=row; break
    except Exception as e:
        print('BUY_FILL_CHECK_ERR',i,repr(e))
if not held:
    print('BUY_ACCEPTED_BUT_HOLDING_NOT_CONFIRMED=True')
    print('STOP_WITHOUT_SELL=True')
    raise SystemExit(2)

print('BUY_HOLDING_CONFIRMED=True ROW=',held_row)
time.sleep(1.2)
print('--- SELL 1 SHARE MARKET ---')
sell=post('/api/us/ordr','ust20001',{
    'stex_tp':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':'','trde_tp':'03'
})
sell_ord=str(sell.get('ord_no') or sell.get('order_no') or '').strip()
sell_ok=(sell.get('return_code') in (None,0) and bool(sell_ord))
print('SELL_ORDER_NO=',sell_ord,'SELL_ACCEPTED=',sell_ok)
if not sell_ok:
    raise SystemExit('SELL_ORDER_NOT_ACCEPTED')

closed=False; final_row=None
for i in range(1,11):
    time.sleep(2.0)
    try:
        _,row,qty,sellable=balance()
        print('SELL_FILL_CHECK',i,'QTY=',qty,'SELLABLE=',sellable)
        final_row=row
        if qty<=0 and sellable<=0:
            closed=True; break
    except Exception as e:
        print('SELL_FILL_CHECK_ERR',i,repr(e))

print('FINAL_POSITION=',final_row)
print('BUY_ACCEPTED=',buy_ok)
print('BUY_HOLDING_CONFIRMED=',held)
print('SELL_ACCEPTED=',sell_ok)
print('SELL_CLOSED_CONFIRMED=',closed)
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=YES')
print('REAL_ACCOUNT_ORDER_CALLS=0')
print('V221_KIWOOM_USA_MOCK_SMOKE_PASS=',bool(buy_ok and held and sell_ok and closed))
