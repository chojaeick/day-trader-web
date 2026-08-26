#!/usr/bin/env python3
from __future__ import annotations
import os, sqlite3, time, json, requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
DB=ROOT/'daytrader.db'
SYMBOL='AAPL'
EXCHANGE='NASD'
STEX_TP='1'
QTY=1

print('=== V224 KIWOOM USA MOCK LIMIT REQUIRED-FIELDS SMOKE ===')
print('TARGET=KIWOOM_MOCK_ACCOUNT USA REAL_ACCOUNT_ALLOWED=NO')
print('ORDER_TYPE=LIMIT_ONLY REQUIRED_FIELD_STEX_TP=YES')

vals=dotenv_values(ENV)
for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE','KIWOOM_MOCK_ORDER_ENABLE'):
    if vals.get(k) is not None: os.environ[k]=str(vals[k])
base=os.environ.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
if base!='https://mockapi.kiwoom.com': raise SystemExit('V224_ABORT non-mock base')
if os.environ.get('KIWOOM_MOCK_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'): raise SystemExit('V224_ABORT mock order disabled')
key=os.environ['KIWOOM_MOCK_APP_KEY']; secret=os.environ['KIWOOM_MOCK_APP_SECRET']

r=requests.post(base+'/oauth2/token',json={'grant_type':'client_credentials','appkey':key,'secretkey':secret},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); d=r.json(); token=d.get('token')
if not token: raise SystemExit('V224_ABORT token failed')
print('TOKEN_OK=True')

def headers(api_id): return {'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id}
def post(path,api_id,body):
    rr=requests.post(base+path,headers=headers(api_id),json=body,timeout=15)
    print('HTTP',api_id,rr.status_code); rr.raise_for_status(); jj=rr.json(); print(api_id,'BODY=',json.dumps(jj,ensure_ascii=False))
    return jj

def live_price():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        t=con.execute('SELECT price,ts FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT 1',(SYMBOL,)).fetchone()
        if t and t['price'] and float(t['price'])>0:
            return float(t['price']), 'ticks', t['ts']
        q=con.execute('SELECT price,updated_at FROM quotes WHERE symbol=?',(SYMBOL,)).fetchone()
        if q and q['price'] and float(q['price'])>0:
            return float(q['price']), 'quotes', q['updated_at']
    finally: con.close()
    return 0.0,None,None

def balance():
    return post('/api/us/acnt','ust21070',{'crnc_code':'USD'})
def holding_qty(body):
    for x in body.get('result_list') or []:
        code=str(x.get('stk_cd') or x.get('symbol') or x.get('symb') or '').upper().strip()
        if code==SYMBOL:
            for k in ('ovrs_cblc_qty','hldg_qty','rmnd_qty','qty'):
                try:
                    if x.get(k) not in (None,''): return float(str(x.get(k)).replace(',',''))
                except: pass
    return 0.0

px,src,ts=live_price(); print('LIVE_PRICE=',px,'SOURCE=',src,'TS=',ts)
if px<=0: raise SystemExit('V224_ABORT no AAPL price')
pre=balance(); preqty=holding_qty(pre); print('PRE_QTY=',preqty)
if preqty>0: raise SystemExit('V224_ABORT existing AAPL holding')

buy_px=round(px*1.01,2)
body_buy={'stex_tp':STEX_TP,'ovrs_excg_cd':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{buy_px:.2f}','trde_tp':'0'}
print('BUY_LIMIT=',buy_px,'BODY_KEYS=',sorted(body_buy))
buy=post('/api/us/ordr','ust20000',body_buy)
accepted=buy.get('return_code')==0
print('BUY_ACCEPTED=',accepted)
if not accepted: raise SystemExit('V224_ABORT buy not accepted')

buy_confirm=False
for i in range(20):
    time.sleep(2); b=balance(); q=holding_qty(b); print('BUY_POLL',i+1,'QTY=',q)
    if q>=1: buy_confirm=True; break
print('BUY_HOLDING_CONFIRMED=',buy_confirm)
if not buy_confirm: raise SystemExit('V224_ABORT buy fill not confirmed')

px2,src2,ts2=live_price(); sell_px=round(max(0.01,px2*0.99),2)
body_sell={'stex_tp':STEX_TP,'ovrs_excg_cd':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{sell_px:.2f}','trde_tp':'0'}
print('SELL_LIMIT=',sell_px,'BODY_KEYS=',sorted(body_sell))
sell=post('/api/us/ordr','ust20001',body_sell)
sell_acc=sell.get('return_code')==0
print('SELL_ACCEPTED=',sell_acc)
if not sell_acc: raise SystemExit('V224_ABORT sell not accepted')

closed=False
for i in range(20):
    time.sleep(2); b=balance(); q=holding_qty(b); print('SELL_POLL',i+1,'QTY=',q)
    if q<=0: closed=True; break
print('SELL_CLOSED_CONFIRMED=',closed)
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=', 'YES' if closed else 'CHECK')
print('V224_KIWOOM_USA_MOCK_SMOKE_PASS=', bool(accepted and buy_confirm and sell_acc and closed))
