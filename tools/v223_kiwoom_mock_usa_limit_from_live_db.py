#!/usr/bin/env python3
from __future__ import annotations
import os, sqlite3, time, json, requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
DB=ROOT/'daytrader.db'
SYMBOL='AAPL'
EXCHANGE='ND'
QTY=1

print('=== V223 KIWOOM USA MOCK LIMIT SMOKE FROM LIVE DB ===')
print('TARGET=KIWOOM_MOCK_ACCOUNT USA REAL_ACCOUNT_ALLOWED=NO')
print('PRICE_SOURCE=LIVE daytrader.db quotes/ticks; NO DOMESTIC TR')

vals=dotenv_values(ENV)
for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE','KIWOOM_MOCK_ORDER_ENABLE'):
    if vals.get(k) is not None: os.environ[k]=str(vals[k])
base=os.environ.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
if base!='https://mockapi.kiwoom.com': raise SystemExit('V223_ABORT non-mock base')
if os.environ.get('KIWOOM_MOCK_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'): raise SystemExit('V223_ABORT mock order disabled')
key=os.environ['KIWOOM_MOCK_APP_KEY']; secret=os.environ['KIWOOM_MOCK_APP_SECRET']

r=requests.post(base+'/oauth2/token',json={'grant_type':'client_credentials','appkey':key,'secretkey':secret},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); d=r.json(); token=d.get('token')
if not token: raise SystemExit('V223_ABORT token failed')
print('TOKEN_OK=True')

def headers(api_id): return {'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id}
def post(path,api_id,body):
    rr=requests.post(base+path,headers=headers(api_id),json=body,timeout=15)
    print('HTTP',api_id,rr.status_code); rr.raise_for_status(); jj=rr.json(); print(api_id,'BODY=',json.dumps(jj,ensure_ascii=False))
    return jj

def live_price():
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    try:
        q=con.execute('SELECT price,updated_at FROM quotes WHERE symbol=?',(SYMBOL,)).fetchone()
        if q and q['price'] and float(q['price'])>0:
            return float(q['price']), 'quotes', q['updated_at']
        t=con.execute('SELECT price,ts FROM ticks WHERE symbol=? ORDER BY id DESC LIMIT 1',(SYMBOL,)).fetchone()
        if t and t['price'] and float(t['price'])>0:
            return float(t['price']), 'ticks', t['ts']
    finally: con.close()
    return 0.0,None,None

def balance():
    return post('/api/us/acnt','ust21070',{'crnc_code':'USD'})
def holding_qty(body):
    rows=body.get('result_list') or []
    for x in rows:
        code=str(x.get('stk_cd') or x.get('symbol') or x.get('symb') or '').upper().strip()
        if code==SYMBOL:
            for k in ('ovrs_cblc_qty','hldg_qty','rmnd_qty','qty'):
                try:
                    if x.get(k) not in (None,''): return float(str(x.get(k)).replace(',',''))
                except: pass
    return 0.0

px,src,ts=live_price()
print('LIVE_PRICE=',px,'SOURCE=',src,'TS=',ts)
if px<=0: raise SystemExit('V223_ABORT no live AAPL price in DB')

pre=balance(); preqty=holding_qty(pre); print('PRE_QTY=',preqty)
if preqty>0: raise SystemExit('V223_ABORT existing AAPL holding')

# aggressive but bounded limit: buy 1% above live, rounded cents
buy_px=round(px*1.01+1e-9,2)
print('BUY_LIMIT=',buy_px)
buy=post('/api/us/ordr','ust20000',{'ovrs_excg_cd':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{buy_px:.2f}','trde_tp':'0'})
accepted=buy.get('return_code')==0
print('BUY_ACCEPTED=',accepted)
if not accepted: raise SystemExit('V223_ABORT buy not accepted')

buy_confirm=False
for i in range(20):
    time.sleep(2); b=balance(); q=holding_qty(b); print('BUY_POLL',i+1,'QTY=',q)
    if q>=1: buy_confirm=True; break
print('BUY_HOLDING_CONFIRMED=',buy_confirm)
if not buy_confirm: raise SystemExit('V223_ABORT buy fill not confirmed')

px2,src2,ts2=live_price(); sell_px=round(max(0.01,px2*0.99),2)
print('SELL_LIMIT=',sell_px,'LIVE_PRICE2=',px2,'SOURCE=',src2,'TS=',ts2)
sell=post('/api/us/ordr','ust20001',{'ovrs_excg_cd':EXCHANGE,'stk_cd':SYMBOL,'ord_qty':str(QTY),'ord_uv':f'{sell_px:.2f}','trde_tp':'0'})
sell_acc=sell.get('return_code')==0
print('SELL_ACCEPTED=',sell_acc)
if not sell_acc: raise SystemExit('V223_ABORT sell not accepted')

closed=False
for i in range(20):
    time.sleep(2); b=balance(); q=holding_qty(b); print('SELL_POLL',i+1,'QTY=',q)
    if q<=0: closed=True; break
print('SELL_CLOSED_CONFIRMED=',closed)
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=', 'YES' if closed else 'CHECK')
print('V223_KIWOOM_USA_MOCK_SMOKE_PASS=', bool(accepted and buy_confirm and sell_acc and closed))
