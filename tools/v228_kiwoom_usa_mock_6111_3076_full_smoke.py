#!/usr/bin/env python3
from __future__ import annotations
import os, sqlite3, time, json, requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
DB=ROOT/'daytrader.db'
TARGET_ACCOUNT='6111-3076'
SYMBOL='AAPL'
STEX_TP='ND'
QTY=1

print('=== V228 KIWOOM USA MOCK 6111-3076 FULL SMOKE ===')
print('TARGET_ACCOUNT=',TARGET_ACCOUNT)
print('TARGET=KIWOOM_MOCK_ACCOUNT USA REAL_ACCOUNT_ALLOWED=NO')
print('REQUIRES=KIWOOM_USA_MOCK_APP_KEY + KIWOOM_USA_MOCK_APP_SECRET')

vals=dotenv_values(ENV)
key=vals.get('KIWOOM_USA_MOCK_APP_KEY')
secret=vals.get('KIWOOM_USA_MOCK_APP_SECRET')
base=(vals.get('KIWOOM_MOCK_REST_BASE') or 'https://mockapi.kiwoom.com').rstrip('/')
if not key or not secret:
    raise SystemExit('V228_ABORT missing KIWOOM_USA_MOCK_APP_KEY/SECRET in /home/ubuntu/day-trader-api/.env')
if base!='https://mockapi.kiwoom.com': raise SystemExit('V228_ABORT non-mock base')

r=requests.post(base+'/oauth2/token',json={'grant_type':'client_credentials','appkey':key,'secretkey':secret},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); token=r.json().get('token')
if not token: raise SystemExit('V228_ABORT token failed')
print('TOKEN_OK=True')

def headers(api_id): return {'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id}
def post(path,api_id,body):
    rr=requests.post(base+path,headers=headers(api_id),json=body,timeout=15)
    print('HTTP',api_id,rr.status_code)
    rr.raise_for_status(); jj=rr.json(); print(api_id,'BODY=',json.dumps(jj,ensure_ascii=False)); return jj

# read-only account identity check via domestic account-number TR; normalize digits only
acct=post('/api/dostk/acnt','ka00001',{})
vals_found=[]
for k,v in acct.items():
    lk=str(k).lower()
    if 'acct' in lk or 'account' in lk or '계좌' in lk:
        vals_found.append((k,str(v)))
print('ACCOUNT_FIELDS=',vals_found)
target_digits=''.join(c for c in TARGET_ACCOUNT if c.isdigit())
matched=any(''.join(c for c in v if c.isdigit())==target_digits for _,v in vals_found)
print('BOUND_TO_TARGET_6111_3076=',matched)
if not matched:
    raise SystemExit('V228_ABORT dedicated USA mock key is not bound to 6111-3076')

def live_price():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        t=con.execute('SELECT price,ts FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT 1',(SYMBOL,)).fetchone()
        if t and t['price'] and float(t['price'])>0: return float(t['price']),'ticks',t['ts']
        q=con.execute('SELECT price,updated_at FROM quotes WHERE symbol=?',(SYMBOL,)).fetchone()
        if q and q['price'] and float(q['price'])>0: return float(q['price']),'quotes',q['updated_at']
    finally: con.close()
    return 0.0,None,None

def balance(): return post('/api/us/acnt','ust21070',{'stex_tp':STEX_TP,'stk_cd':SYMBOL})
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
if px<=0: raise SystemExit('V228_ABORT no AAPL price')
pre=balance(); preqty=holding_qty(pre); print('PRE_QTY=',preqty)
if preqty>0: raise SystemExit('V228_ABORT existing AAPL holding')

buy_px=round(px*1.01,2)
buy_body={'stex_tp':STEX_TP,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{buy_px:.2f}','trde_tp':'00'}
print('BUY_LIMIT=',buy_px,'BODY=',buy_body)
buy=post('/api/us/ordr','ust20000',buy_body)
accepted=buy.get('return_code')==0; print('BUY_ACCEPTED=',accepted,'ORDER_NO=',buy.get('ord_no'))
if not accepted: raise SystemExit('V228_ABORT buy not accepted')

buy_confirm=False
for i in range(20):
    time.sleep(2); q=holding_qty(balance()); print('BUY_POLL',i+1,'QTY=',q)
    if q>=1: buy_confirm=True; break
print('BUY_HOLDING_CONFIRMED=',buy_confirm)
if not buy_confirm: raise SystemExit('V228_ABORT buy fill not confirmed')

px2,src2,ts2=live_price(); sell_px=round(max(0.01,px2*0.99),2)
sell_body={'stex_tp':STEX_TP,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{sell_px:.2f}','trde_tp':'00'}
print('SELL_LIMIT=',sell_px,'BODY=',sell_body,'LIVE_PRICE2=',px2,'SOURCE=',src2,'TS=',ts2)
sell=post('/api/us/ordr','ust20001',sell_body)
sell_acc=sell.get('return_code')==0; print('SELL_ACCEPTED=',sell_acc,'ORDER_NO=',sell.get('ord_no'))
if not sell_acc: raise SystemExit('V228_ABORT sell not accepted')

closed=False
for i in range(20):
    time.sleep(2); q=holding_qty(balance()); print('SELL_POLL',i+1,'QTY=',q)
    if q<=0: closed=True; break
print('SELL_CLOSED_CONFIRMED=',closed)
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=', 'YES' if closed else 'CHECK')
print('V228_KIWOOM_USA_MOCK_SMOKE_PASS=', bool(accepted and buy_confirm and sell_acc and closed))
