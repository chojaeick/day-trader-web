#!/usr/bin/env python3
from __future__ import annotations
import os, json, time, sqlite3, requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
DB=ROOT/'daytrader.db'
TARGET_MANUAL='SOXL'
TEST_SYMBOL='AAPL'
STEX='ND'

print('=== V228 US MOCK ACCOUNT CONTEXT PROBE -> ORDER IF MATCHED ===')
print('USES_EXISTING_SINGLE_MOCK_KEY=YES REAL_ACCOUNT_ALLOWED=NO')
print('STEP1=CHECK_FULL_US_BALANCE_FOR_MANUAL_SOXL STEP2=ONLY_IF_MATCHED_TEST_AAPL_BUY_SELL')

vals=dotenv_values(ENV)
for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE','KIWOOM_MOCK_ORDER_ENABLE'):
    if vals.get(k) is not None: os.environ[k]=str(vals[k])
base=os.environ.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
if base!='https://mockapi.kiwoom.com': raise SystemExit('V228_ABORT non-mock base')
if os.environ.get('KIWOOM_MOCK_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'): raise SystemExit('V228_ABORT mock order disabled')

r=requests.post(base+'/oauth2/token',json={
    'grant_type':'client_credentials',
    'appkey':os.environ['KIWOOM_MOCK_APP_KEY'],
    'secretkey':os.environ['KIWOOM_MOCK_APP_SECRET']
},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); token=r.json().get('token')
if not token: raise SystemExit('V228_ABORT token failed')
print('TOKEN_OK=True')

def headers(api_id):
    return {'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id}

def post(path,api_id,body):
    rr=requests.post(base+path,headers=headers(api_id),json=body,timeout=15)
    print('HTTP',api_id,rr.status_code)
    rr.raise_for_status(); jj=rr.json()
    print(api_id,'RETURN_CODE=',jj.get('return_code'),'RETURN_MSG=',jj.get('return_msg'))
    return jj

def all_us_balance():
    # Official ust21070: stex_tp/stk_cd are optional; omit both to request all US holdings.
    return post('/api/us/acnt','ust21070',{})

def rows_of(body):
    for key in ('result_list','rows','list','output','output1'):
        v=body.get(key)
        if isinstance(v,list): return v
    return []

def row_symbol(x):
    for k in ('stk_cd','symbol','symb','pdno','ovrs_pdno'):
        v=x.get(k)
        if v: return str(v).upper().strip()
    return ''

def row_qty(x):
    for k in ('ovrs_cblc_qty','hldg_qty','rmnd_qty','qty','ord_psbl_qty','hold_qty'):
        v=x.get(k)
        if v not in (None,''):
            try: return float(str(v).replace(',',''))
            except: pass
    return 0.0

def find_holding(body,sym):
    for x in rows_of(body):
        if row_symbol(x)==sym:
            return x,row_qty(x)
    return None,0.0

bal=all_us_balance()
rows=rows_of(bal)
print('US_BALANCE_ROW_COUNT=',len(rows))
print('US_BALANCE_SYMBOLS=',[(row_symbol(x),row_qty(x)) for x in rows[:30]])
soxl_row,soxl_qty=find_holding(bal,TARGET_MANUAL)
print('MANUAL_SOXL_VISIBLE=',bool(soxl_row),'SOXL_QTY=',soxl_qty)

if not soxl_row or soxl_qty<=0:
    print('V228_CONTEXT_MATCH=False')
    print('DIAGNOSIS=REST_MOCK_US_ACCOUNT_CONTEXT_DOES_NOT_SEE_MANUAL_SOXL_POSITION')
    print('ORDER_TEST_SKIPPED=YES')
    print('NEXT=COMPARE_US_MOCK_PARTICIPATION_SESSION_OR_TOKEN_CONTEXT; DO_NOT_CHANGE_APP_KEY_YET')
    raise SystemExit(2)

print('V228_CONTEXT_MATCH=True')
print('SAME_US_MOCK_CONTEXT_CONFIRMED_BY_MANUAL_SOXL=YES')

# only after context match, test AAPL 1-share limit round trip

def live_price(sym):
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        t=con.execute('SELECT price,ts FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT 1',(sym,)).fetchone()
        if t and t['price'] and float(t['price'])>0: return float(t['price']),t['ts']
        q=con.execute('SELECT price,updated_at FROM quotes WHERE symbol=?',(sym,)).fetchone()
        if q and q['price'] and float(q['price'])>0: return float(q['price']),q['updated_at']
    finally: con.close()
    return 0.0,None

px,ts=live_price(TEST_SYMBOL)
print('AAPL_PRICE=',px,'TS=',ts)
if px<=0: raise SystemExit('V228_ABORT no AAPL price')

# precheck no AAPL holding
_,pre_qty=find_holding(all_us_balance(),TEST_SYMBOL)
print('AAPL_PRE_QTY=',pre_qty)
if pre_qty>0: raise SystemExit('V228_ABORT existing AAPL holding')

buy_px=round(px*1.01,2)
buy_body={'stex_tp':STEX,'stk_cd':TEST_SYMBOL,'ord_qty':'1','ord_uv':f'{buy_px:.2f}','trde_tp':'00'}
print('BUY_LIMIT=',buy_px)
buy=post('/api/us/ordr','ust20000',buy_body)
buy_ok=(buy.get('return_code')==0)
print('BUY_ACCEPTED=',buy_ok,'ORDER_NO=',buy.get('ord_no'))
if not buy_ok: raise SystemExit('V228_ABORT buy rejected')

buy_fill=False
for i in range(20):
    time.sleep(2)
    _,q=find_holding(all_us_balance(),TEST_SYMBOL)
    print('BUY_POLL',i+1,'AAPL_QTY=',q)
    if q>=1: buy_fill=True; break
print('BUY_HOLDING_CONFIRMED=',buy_fill)
if not buy_fill: raise SystemExit('V228_ABORT buy fill not confirmed')

px2,_=live_price(TEST_SYMBOL)
sell_px=round(max(0.01,px2*0.99),2)
sell_body={'stex_tp':STEX,'stk_cd':TEST_SYMBOL,'ord_qty':'1','ord_uv':f'{sell_px:.2f}','trde_tp':'00'}
print('SELL_LIMIT=',sell_px)
sell=post('/api/us/ordr','ust20001',sell_body)
sell_ok=(sell.get('return_code')==0)
print('SELL_ACCEPTED=',sell_ok,'ORDER_NO=',sell.get('ord_no'))
if not sell_ok: raise SystemExit('V228_ABORT sell rejected')

closed=False
for i in range(20):
    time.sleep(2)
    _,q=find_holding(all_us_balance(),TEST_SYMBOL)
    print('SELL_POLL',i+1,'AAPL_QTY=',q)
    if q<=0: closed=True; break
print('SELL_CLOSED_CONFIRMED=',closed)
print('V228_KIWOOM_USA_MOCK_SMOKE_PASS=',bool(buy_ok and buy_fill and sell_ok and closed))
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=', 'YES' if closed else 'CHECK')
