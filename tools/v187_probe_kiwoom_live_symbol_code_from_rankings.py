#!/usr/bin/env python3
from __future__ import annotations
import os, sys, json, time
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
VENV=RUNTIME/'venv/bin/python3'
if Path(sys.executable).resolve()!=VENV.resolve() and VENV.exists():
    os.execv(str(VENV), [str(VENV), __file__])

sys.path.insert(0,str(RUNTIME))
from live_server.config import Settings
from live_server.db import DB
from live_server.kiwoom import KiwoomClient

TARGETS=['AMD','AMZN','ARM','AVGO','GOOGL','INTC','NFLX','NVDA','ORCL','SMCI','TSM','PLTR']
print('=== V187 PROBE KIWOOM LIVE SYMBOL CODE FROM RANKINGS ===')
print('READ_ONLY=YES STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE SERVICE_MUTATION=NONE')
s=Settings(); db=DB(s.db_path); k=KiwoomClient(s,db)
try:
    k.get_token()
except Exception as e:
    print('TOKEN_ERROR',repr(e)); raise SystemExit(2)

hits={x:[] for x in TARGETS}
for api_id,body_builder,path in [
    ('usa20530',lambda stex:{'stex_tp':stex,'inds_cd':'','stk_tp':'0','trde_qty_tp':'0','qry_tp':'0','stk_cnd':'0','pric_cnd':'0','trde_prica_cnd':'0'},'/api/us/rkinfo'),
    ('usa20910',lambda stex:{'stex_tp':stex,'inds_cd':'','inds_cls_tp':'0','sort_tp':'1','stk_tp':'0','stk_cnd':'0','pric_cnd':'0','trde_prica_cnd':'0','trde_qty_tp':''},'/api/us/rkinfo'),
    ('usa20520',lambda stex:{'stex_tp':stex,'inds_cd':'','tm':'5','stk_tp':'0','stk_cnd':'0','pric_cnd':'0','trde_prica_cnd':'0','trde_qty_tp':'0'},'/api/us/stkinfo'),
]:
    for stex in ('0','1','2','3'):
        try:
            import requests
            r=requests.post(s.rest_base+path,headers=k.headers(api_id),json=body_builder(stex),timeout=20)
            d=r.json()
            rows=d.get('result_list') or []
            print('PROBE',api_id,'STEX',stex,'RC',d.get('return_code'),'ROWS',len(rows))
            for row in rows:
                if not isinstance(row,dict): continue
                raw=str(row.get('stk_cd') or '').upper().strip()
                raw_name=str(row.get('stk_nm') or row.get('name') or '').upper().strip()
                for t in TARGETS:
                    if raw==t or raw.endswith(t) or t in raw_name:
                        rec={k0:row.get(k0) for k0 in ('stk_cd','stex_tp','stk_nm','cur_prc') if k0 in row}
                        rec['api_id']=api_id; rec['query_stex']=stex
                        hits[t].append(rec)
            time.sleep(0.2)
        except Exception as e:
            print('ERROR',api_id,stex,repr(e))

for t in TARGETS:
    print('TARGET',t,'HITS',len(hits[t]))
    for rec in hits[t][:10]:
        print(' HIT',json.dumps(rec,ensure_ascii=False,sort_keys=True))

# Also REST quote brute-force to see which exchange code is accepted for the target symbols.
for t in TARGETS:
    ok=[]
    for ex in ('ND','NY','NA'):
        try:
            q=k.quote(t,ex)
            ok.append((ex,True,{kk:q.get(kk) for kk in ('stk_cd','stex_tp','stk_nm','cur_prc','last_pric') if isinstance(q,dict) and kk in q}))
        except Exception as e:
            ok.append((ex,False,str(e)[:180]))
    print('QUOTE_MATRIX',t,ok)

print('NEXT=IF_RANKING_RETURNS_DIFFERENT_STK_CD_USE_THAT_JMCODE; IF_STK_CD_MATCHES_AND_QUOTE_WORKS_BUT_F5_NO_TICK_TEST_KIWOOM_REALTIME_ELIGIBILITY_WITH_REG_ACK_PER_SYMBOL')
