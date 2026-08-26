#!/usr/bin/env python3
from __future__ import annotations
import os,sys,time,json,requests
from pathlib import Path

ENV=Path('/home/ubuntu/day-trader-api/.env')
if not ENV.exists(): raise SystemExit('V222_ABORT missing runtime .env')
for line in ENV.read_text().splitlines():
    s=line.strip()
    if not s or s.startswith('#') or '=' not in s: continue
    k,v=s.split('=',1); k=k.strip(); v=v.strip().strip('"').strip("'")
    if k in {'KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_ORDER_ENABLE','KIWOOM_MOCK_REST_BASE'}:
        os.environ[k]=v

BASE=os.environ.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
KEY=os.environ.get('KIWOOM_MOCK_APP_KEY','')
SECRET=os.environ.get('KIWOOM_MOCK_APP_SECRET','')
EN=os.environ.get('KIWOOM_MOCK_ORDER_ENABLE','0').lower() in ('1','true','yes','on')
if BASE!='https://mockapi.kiwoom.com': raise SystemExit(f'V222_ABORT non-mock base {BASE}')
if not KEY or not SECRET or not EN: raise SystemExit('V222_ABORT mock credentials/order-enable invalid')

SYMBOL='AAPL'; EXCHANGE='NASD'; QTY=1
print('=== V222 KIWOOM USA MOCK LIMIT-ORDER SMOKE ===')
print('TARGET=KIWOOM_MOCK_ACCOUNT USA')
print('REAL_ACCOUNT_ALLOWED=NO')
print('SYMBOL=',SYMBOL,'QTY=',QTY,'ORDER_TYPE=LIMIT_ONLY')

r=requests.post(BASE+'/oauth2/token',json={'grant_type':'client_credentials','appkey':KEY,'secretkey':SECRET},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); d=r.json(); token=d.get('token')
if not token: raise SystemExit('V222_ABORT token failed '+str(d))
print('TOKEN_OK=True')

def post(path,api_id,body):
    rr=requests.post(BASE+path,headers={'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':api_id},json=body,timeout=15)
    print('HTTP',api_id,rr.status_code)
    try: out=rr.json()
    except Exception: out={'raw':rr.text}
    print(api_id,'BODY=',json.dumps(out,ensure_ascii=False))
    return out

# Quote query: use USA basic quote TR. If unavailable, abort rather than guess.
quote=None
for api_id,body in [
    ('ka10001',{'stk_cd':SYMBOL}),
]:
    try:
        q=post('/api/dostk/stkinfo',api_id,body)
        for k in ('cur_prc','last','price','close'):
            if q.get(k) not in (None,''):
                try: quote=abs(float(str(q[k]).replace(',','')))
                except Exception: pass
                if quote: break
    except Exception:
        pass
    if quote: break

if not quote:
    # fallback to live local API quote, read only
    try:
        rr=requests.get('http://127.0.0.1:8000/api/v4/USA/frozen-paper',timeout=5)
        data=rr.json() if rr.ok else {}
        rows=data.get('rows') or []
        row=next((x for x in rows if str(x.get('symbol','')).upper()==SYMBOL),None)
        if row:
            for k in ('price','current_price','close'):
                try:
                    quote=float(row.get(k) or 0)
                except Exception: quote=0
                if quote: break
    except Exception: pass

if not quote: raise SystemExit('V222_ABORT no safe current price for limit order')
# aggressive limit: buy slightly above, sell slightly below current, 2 decimals
buy_px=round(quote*1.003,2)
sell_px=round(quote*0.997,2)
print('REFERENCE_PRICE=',quote,'BUY_LIMIT=',buy_px,'SELL_LIMIT=',sell_px)

# precheck holdings
bal=post('/api/us/acnt','ust21070',{'crnc_code':'USD'})
pre=0.0
for x in bal.get('result_list') or []:
    sym=str(x.get('stk_cd') or x.get('symbol') or '').upper()
    if sym==SYMBOL:
        try: pre=float(str(x.get('rmnd_qty') or x.get('qty') or '0').replace(',',''))
        except Exception: pre=0
print('PRE_QTY=',pre)
if pre>0: raise SystemExit('V222_ABORT existing AAPL position present; refuse collision')

buy=post('/api/us/ordr','ust20000',{
    'ovrs_excg_cd':EXCHANGE,
    'stk_cd':SYMBOL,
    'ord_qty':str(QTY),
    'ord_uv':f'{buy_px:.2f}',
    'trde_tp':'0',
})
buy_ok=buy.get('return_code') in (None,0) and bool(buy.get('ord_no') or buy.get('order_no') or buy.get('ord_no'))
print('BUY_ACCEPTED=',buy_ok,'BUY_ORDER_NO=',buy.get('ord_no') or buy.get('order_no') or '')
if not buy_ok: raise SystemExit('V222_ABORT buy not accepted')

held=False
for i in range(15):
    time.sleep(2)
    b=post('/api/us/acnt','ust21070',{'crnc_code':'USD'})
    qty=0.0
    for x in b.get('result_list') or []:
        sym=str(x.get('stk_cd') or x.get('symbol') or '').upper()
        if sym==SYMBOL:
            for k in ('rmnd_qty','qty','hldg_qty'):
                if x.get(k) not in (None,''):
                    try: qty=float(str(x.get(k)).replace(',',''))
                    except Exception: qty=0
                    break
    print('BUY_CONFIRM_ATTEMPT',i+1,'QTY=',qty)
    if qty>=1:
        held=True; break
print('BUY_HOLDING_CONFIRMED=',held)
if not held: raise SystemExit('V222_ABORT buy accepted but holding not confirmed')

sell=post('/api/us/ordr','ust20001',{
    'ovrs_excg_cd':EXCHANGE,
    'stk_cd':SYMBOL,
    'ord_qty':str(QTY),
    'ord_uv':f'{sell_px:.2f}',
    'trde_tp':'0',
})
sell_ok=sell.get('return_code') in (None,0) and bool(sell.get('ord_no') or sell.get('order_no') or sell.get('ord_no'))
print('SELL_ACCEPTED=',sell_ok,'SELL_ORDER_NO=',sell.get('ord_no') or sell.get('order_no') or '')
if not sell_ok: raise SystemExit('V222_ABORT sell not accepted')

closed=False
for i in range(15):
    time.sleep(2)
    b=post('/api/us/acnt','ust21070',{'crnc_code':'USD'})
    qty=0.0
    for x in b.get('result_list') or []:
        sym=str(x.get('stk_cd') or x.get('symbol') or '').upper()
        if sym==SYMBOL:
            for k in ('rmnd_qty','qty','hldg_qty'):
                if x.get(k) not in (None,''):
                    try: qty=float(str(x.get(k)).replace(',',''))
                    except Exception: qty=0
                    break
    print('SELL_CONFIRM_ATTEMPT',i+1,'QTY=',qty)
    if qty<=0:
        closed=True; break
print('SELL_CLOSED_CONFIRMED=',closed)
print('KIWOOM_MOCK_UI_SHOULD_SHOW_BUY_SELL=', 'YES' if (buy_ok and held and sell_ok and closed) else 'NO')
print('V222_KIWOOM_USA_MOCK_SMOKE_PASS=',bool(buy_ok and held and sell_ok and closed))
