#!/usr/bin/env python3
from __future__ import annotations
import os,json,requests
from pathlib import Path
from dotenv import dotenv_values

ROOT=Path('/home/ubuntu/day-trader-api')
ENV=ROOT/'.env'
print('=== V229 US MOCK ACCOUNT TR MATRIX (READ ONLY) ===')
print('ORDER=NONE SERVICE_MUTATION=NONE REAL_ACCOUNT_ALLOWED=NO')
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
    try:
        rr=requests.post(base+path,headers=h,json=body,timeout=15)
        out={'http':rr.status_code}
        try: out['json']=rr.json()
        except Exception: out['text']=rr.text[:500]
        return out
    except Exception as e:
        return {'error':repr(e)}

def compact(name,res):
    j=res.get('json') if isinstance(res,dict) else None
    print('---',name,'---')
    if not isinstance(j,dict):
        print(res); return
    print('HTTP=',res.get('http'),'RETURN_CODE=',j.get('return_code'),'RETURN_MSG=',j.get('return_msg'))
    for k in ('acctNo','crnc_code','frgn_cblc','ovrs_stk_evlt_amt','tot_evlt_amt','ord_psbl_amt'):
        if k in j: print(k,'=',j.get(k))
    for k,v in j.items():
        if isinstance(v,list):
            print(k,'COUNT=',len(v))
            if v: print(k,'SAMPLE=',json.dumps(v[0],ensure_ascii=False)[:800])

# Domestic account number only as control; does NOT imply US account binding.
compact('ka00001 domestic account control',post('/api/dostk/acnt','ka00001',{}))
# US account matrix. These are read-only account TRs.
compact('ust21070 US holdings ALL',post('/api/us/acnt','ust21070',{}))
compact('ust21070 SOXL only',post('/api/us/acnt','ust21070',{'stex_tp':'NY','stk_cd':'SOXL'}))
compact('ust21050 US open orders SOXL',post('/api/us/acnt','ust21050',{'stex_tp':'NY','stk_cd':'SOXL'}))
compact('ust21110 US cash/deposit',post('/api/us/acnt','ust21110',{}))
compact('ust21510 US today order/fill check',post('/api/us/acnt','ust21510',{}))
# US quote control proves token can reach US product APIs even if account context is absent.
compact('usa20100 US quote control SOXL',post('/api/us/stkinfo','usa20100',{'stex_tp':'NY','stk_cd':'SOXL'}))
print('NEXT=IF_US_QUOTE_OK_BUT_ACCOUNT_TRS_EMPTY_OR_ENDED=>US_MOCK_ACCOUNT_SESSION/PARTICIPATION_CONTEXT_ISSUE; IF_ANY_ACCOUNT_TR_SEES_SOXL=>USE_THAT_TR_SCHEMA_FOR_ORDER_VERIFY')
