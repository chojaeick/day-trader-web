#!/usr/bin/env python3
import os, time, json
from live_server.kiwoom_mock_broker import KiwoomMockBroker

print('=== KIWOOM MOCK FILLCHECK V100B ===')
print('NO ORDER IS SENT BY THIS SCRIPT')

b=KiwoomMockBroker()
acct=b.account_number()
print('ACCOUNT', acct[:4]+'****'+acct[-2:])
time.sleep(1.2)

checks = [
    ('FILLS ka10076', 'ka10076', {'qry_tp':'0','sell_tp':'0','stex_tp':'1'}),
    ('EXEC_BALANCE kt00004', 'kt00004', {'qry_tp':'0','dmst_stex_tp':'KRX'}),
]

for name, api_id, body in checks:
    print('\n---', name, '---')
    try:
        d=b.request_account(api_id, body)
        print('RETURN_CODE', d.get('return_code'))
        print('RETURN_MSG', d.get('return_msg'))
        # avoid dumping excessive data; print keys and first record-ish payloads
        print('KEYS', sorted(d.keys()))
        for k,v in d.items():
            if isinstance(v, list):
                print(k, 'COUNT', len(v))
                if v:
                    print(k+'_FIRST', json.dumps(v[0], ensure_ascii=False))
            elif k not in ('return_code','return_msg'):
                print(k, json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list)) else v)
        time.sleep(1.2)
    except Exception as e:
        print('ERROR', type(e).__name__, e)
        time.sleep(1.2)

print('\nDONE')
