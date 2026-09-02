from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()
old='''        self._williams_mock_account_sync_mono=_now_mono
        """V115: restore current Kiwoom mock holdings once per process."""
        if getattr(self, "_williams_mock_account_synced", False):
            return
'''
new='''        self._williams_mock_account_sync_mono=_now_mono
        """V115: restore current Kiwoom mock holdings once per process."""
        if getattr(self, "_williams_mock_account_synced", False):
            bal = broker.request_account(
                "kt00004",
                {"qry_tp":"0", "dmst_stex_tp":"KRX"},
            )
            self._williams_mock_account_cache = bal
            self._williams_mock_account_cache_mono = _time.monotonic()
            return
'''
if new in s:
    print('V23 KR ACCOUNT CACHE REFRESH ALREADY CONNECTED')
elif old not in s:
    raise SystemExit('EXACT SYNC BLOCK NOT FOUND - NOTHING CHANGED')
else:
    s=s.replace(old,new,1)
    p.write_text(s)
    print('V23 KR ACCOUNT CACHE REFRESH CONNECTED')
