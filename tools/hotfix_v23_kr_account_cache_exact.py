from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()
old='''        bal = broker.request_account(\n            "kt00004",\n            {"qry_tp":"0", "dmst_stex_tp":"KRX"},\n        )\n'''
new='''        bal = broker.request_account(\n            "kt00004",\n            {"qry_tp":"0", "dmst_stex_tp":"KRX"},\n        )\n        self._williams_mock_account_cache = bal\n        self._williams_mock_account_cache_mono = _now_mono\n'''
if old not in s:
    raise SystemExit('EXACT KT00004 BLOCK NOT FOUND - NOTHING CHANGED')
if 'self._williams_mock_account_cache = bal' not in s:
    s=s.replace(old,new,1)
    p.write_text(s)
    print('V23 KR ACCOUNT CACHE EXACT CONNECTED')
else:
    print('V23 KR ACCOUNT CACHE EXACT ALREADY CONNECTED')
