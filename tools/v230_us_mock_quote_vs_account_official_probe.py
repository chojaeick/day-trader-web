#!/usr/bin/env python3
from __future__ import annotations
import os,json,requests,time
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
print('=== V230 US MOCK OFFICIAL QUOTE VS ACCOUNT PROBE ===')
print('ORDER=NONE MUTATION=NONE REAL_ACCOUNT_ALLOWED=NO')
vals=dotenv_values(ENV)
for k in ('KIWOOM_MOCK_APP_KEY','KIWOOM_MOCK_APP_SECRET','KIWOOM_MOCK_REST_BASE'):
    if vals.get(k) is not None: os.environ[k]=str(vals[k])
base=os.environ.get('KIWOOM_MOCK_REST_BASE','').rstrip('/')
if base!='https://mockapi.kiwoom.com': raise SystemExit('ABORT_NON_MOCK_BASE')

r=requests.post(base+'/oauth2/token',json={'grant_type':'client_credentials','appkey':os.environ['KIWOOM_MOCK_APP_KEY'],'secretkey':os.environ['KIWOOM_MOCK_APP_SECRET']},headers={'Content-Type':'application/json;charset=UTF-8'},timeout=15)
r.raise_for_status(); tok=r.json().get('token')
if not tok: raise SystemExit('TOKEN_FAIL')
print('TOKEN_OK=True')

def post(path,api_id,body):
    h={'Content-Type':'application/json;charset=UTF-8','authorization':f'Bearer {tok}','api-id':api_id}
    rr=requests.post(base+path,headers=h,json=body,timeout=15)
    try: j=rr.json()
    except Exception: j={'raw':rr.text[:500]}
    print('API',api_id,'PATH',path,'HTTP',rr.status_code,'RETURN_CODE',j.get('return_code'),'RETURN_MSG',j.get('return_msg'))
    print('BODY',json.dumps(j,ensure_ascii=False)[:1200])
    return rr,j

# Official account endpoint/schema control
post('/api/us/acnt','ust21070',{})
time.sleep(1.2)

# Probe likely official US stock-info endpoint variants for usa20100, no order side effects.
paths=['/api/us/stkinfo','/api/us/stock','/api/us/mrkcond','/api/us/stk']
quote_ok=False
for p in paths:
    time.sleep(1.2)
    rr,j=post(p,'usa20100',{'stex_tp':'NY','stk_cd':'SOXL'})
    if rr.status_code==200 and j.get('return_code')==0:
        quote_ok=True
        print('QUOTE_PATH_OK=',p)
        break

print('US_QUOTE_OK=',quote_ok)
print('ACCOUNT_EMPTY_EXPECTED_FROM_PRIOR=True')
print('NEXT=IF_QUOTE_OK_AND_ACCOUNT_EMPTY=>US_MOCK_ACCOUNT_CONTEXT_ISSUE; IF_QUOTE_NOT_OK=>FETCH_EXACT_OFFICIAL usa20100 URL BEFORE ANY ORDER TEST')
