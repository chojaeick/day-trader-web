#!/usr/bin/env python3
from __future__ import annotations
import os,json,requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
TARGET='6111-3076'
print('=== V227 IDENTIFY MOCK APPKEY BOUND ACCOUNT (READ ONLY) ===')
print('TARGET_ACCOUNT=',TARGET)
print('ORDER=NONE MUTATION=NONE REAL_ACCOUNT_ALLOWED=NO')

vals=dotenv_values(ENV)
for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE'):
    if vals.get(k) is not None: os.environ[k]=str(vals[k])
base=os.environ.get('KIWOOM_MOCK_REST_BASE','https://mockapi.kiwoom.com').rstrip('/')
if base!='https://mockapi.kiwoom.com': raise SystemExit('V227_ABORT non-mock base')
key=os.environ.get('KIWOOM_MOCK_APP_KEY',''); secret=os.environ.get('KIWOOM_MOCK_APP_SECRET','')
if not key or not secret: raise SystemExit('V227_ABORT missing mock key/secret')

r=requests.post(base+'/oauth2/token',json={'grant_type':'client_credentials','appkey':key,'secretkey':secret},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); d=r.json(); token=d.get('token')
if not token: raise SystemExit('V227_ABORT token failed')
print('TOKEN_OK=True')

h={'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {token}','api-id':'ka00001'}
rr=requests.post(base+'/api/dostk/acnt',headers=h,json={},timeout=15)
print('HTTP ka00001',rr.status_code); rr.raise_for_status(); body=rr.json()
# redact any unrelated fields and print only account-like candidates
cands=[]
def walk(x,path=''):
    if isinstance(x,dict):
        for k,v in x.items():
            p=f'{path}.{k}' if path else k
            lk=k.lower()
            if any(s in lk for s in ('acct','acnt','account','계좌')) and isinstance(v,(str,int,float)):
                cands.append((p,str(v)))
            walk(v,p)
    elif isinstance(x,list):
        for i,v in enumerate(x): walk(v,f'{path}[{i}]')
walk(body)
print('RETURN_CODE=',body.get('return_code'),'RETURN_MSG=',body.get('return_msg'))
print('ACCOUNT_FIELDS=',cands)
normalized_target=''.join(ch for ch in TARGET if ch.isdigit())
matched=False
for _,v in cands:
    nv=''.join(ch for ch in str(v) if ch.isdigit())
    if normalized_target and normalized_target in nv:
        matched=True
print('BOUND_TO_TARGET_6111_3076=',matched)
print('NEXT=', 'USE_THIS_KEY_FOR_USA' if matched else 'NEED_6111_3076_BOUND_APPKEY_SECRET')
