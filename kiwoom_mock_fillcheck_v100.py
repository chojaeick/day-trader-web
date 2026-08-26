#!/usr/bin/env python3
import os, time, json
from live_server.kiwoom_mock_broker import KiwoomMockBroker

print('=== KIWOOM MOCK FILLCHECK V100 ===')
print('NO ORDER IS SENT BY THIS SCRIPT')

b=KiwoomMockBroker()
acct=b.account_number()
print('ACCOUNT', acct[:4]+'****'+acct[-2:])

time.sleep(1.2)

checks=[
    ('kt00004','EXEC_BALANCE'),
    ('ka10076','FILLS'),
    ('kt00017','ACCOUNT_EVAL'),
]

for api_id,label in checks:
    try:
        d=b.request_account(api_id,{})
        print('\n---',label,api_id,'---')
        print('RETURN_CODE', d.get('return_code'))
        print('RETURN_MSG', d.get('return_msg'))
        print('KEYS', sorted(d.keys()))
        # Print compact, safe subset. Mock account only.
        print(json.dumps(d, ensure_ascii=False, indent=2)[:5000])
    except Exception as e:
        print('\n---',label,api_id,'ERROR ---')
        print(type(e).__name__, str(e))
    time.sleep(1.2)

print('\nDONE')
