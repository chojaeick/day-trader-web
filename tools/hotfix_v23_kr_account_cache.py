from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

# Add a short TTL guard at the start of account sync so tracker rows do not
# hammer Kiwoom /acnt once per symbol. Existing sync logic remains unchanged.
needle='''    def _williams_mock_sync_account(self, broker):\n'''
insert='''    def _williams_mock_sync_account(self, broker):\n        import time as _time\n        _now_mono=_time.monotonic()\n        _last_sync=float(getattr(self, '_williams_mock_account_sync_mono', 0.0) or 0.0)\n        if _now_mono-_last_sync < 15.0:\n            return\n        self._williams_mock_account_sync_mono=_now_mono\n'''
if needle not in s:
    raise SystemExit('ACCOUNT SYNC TARGET NOT FOUND - NOTHING CHANGED')
s=s.replace(needle,insert,1)

# Entry sizing currently performs a second immediate kt00004 request after the
# sync. Reuse a short-lived account snapshot saved by the sync instead.
# First make sync retain its successful raw response. Locate its request line
# without depending on surrounding comments/runtime drift.
request_variants=[
    '_bal=broker.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})',
    "_bal=broker.request_account('kt00004', {'qry_tp':'0','dmst_stex_tp':'KRX'})",
    'bal=broker.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})',
    "bal=broker.request_account('kt00004', {'qry_tp':'0','dmst_stex_tp':'KRX'})",
]
found=None
for v in request_variants:
    if v in s:
        found=v
        break
if found is None:
    # Do not risk a blind rewrite. The TTL guard alone already removes the
    # per-symbol sync storm; leave entry sizing untouched if runtime differs.
    p.write_text(s)
    print('V23 KR ACCOUNT SYNC THROTTLE CONNECTED (15s)')
    raise SystemExit(0)

# Save whichever local variable receives the successful response.
var=found.split('=')[0].strip()
s=s.replace(found, found+f"\n        self._williams_mock_account_cache={var}\n        self._williams_mock_account_cache_mono=_time.monotonic()",1)

# Replace only the later direct sizing request, if exact runtime line exists.
sizing='_bal=b.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})'
if sizing in s:
    repl='''_cache_age=_time.monotonic()-float(getattr(self,'_williams_mock_account_cache_mono',0.0) or 0.0)\n                _bal=getattr(self,'_williams_mock_account_cache',None) if _cache_age<15.0 else None\n                if not isinstance(_bal,dict):\n                    _bal=b.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})\n                    self._williams_mock_account_cache=_bal\n                    self._williams_mock_account_cache_mono=_time.monotonic()'''
    s=s.replace(sizing,repl,1)

p.write_text(s)
print('V23 KR ACCOUNT CACHE CONNECTED (15s)')
