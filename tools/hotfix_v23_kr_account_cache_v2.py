from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

# Cache the successful account response inside the existing sync routine.
# Runtime variants commonly use either bal or _bal.
marker='self._williams_mock_account_cache='
if marker not in s:
    candidates=[
        ('bal=broker.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})','bal'),
        ("bal=broker.request_account('kt00004', {'qry_tp':'0','dmst_stex_tp':'KRX'})",'bal'),
        ('_bal=broker.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})','_bal'),
        ("_bal=broker.request_account('kt00004', {'qry_tp':'0','dmst_stex_tp':'KRX'})",'_bal'),
    ]
    hit=None
    for line,var in candidates:
        if line in s:
            hit=(line,var); break
    if hit:
        line,var=hit
        s=s.replace(line,line+f"\n        self._williams_mock_account_cache={var}\n        self._williams_mock_account_cache_mono=_time.monotonic()",1)

# Replace the entry-sizing account call with the just-synced cache. This is the
# second /acnt call that currently triggers 429. If cache is absent/stale, skip
# this entry attempt instead of hammering Kiwoom again; next tracker cycle can retry.
sizing='_bal=b.request_account("kt00004", {"qry_tp":"0","dmst_stex_tp":"KRX"})'
if sizing not in s:
    raise SystemExit('ENTRY ACCOUNT TARGET NOT FOUND - NOTHING CHANGED')
repl="""_bal=getattr(self,'_williams_mock_account_cache',None)\n                _cache_age=_time.monotonic()-float(getattr(self,'_williams_mock_account_cache_mono',0.0) or 0.0)\n                if not isinstance(_bal,dict) or _cache_age>=15.0:\n                    return"""
s=s.replace(sizing,repl,1)
p.write_text(s)
print('V23 KR ENTRY ACCOUNT CACHE V2 CONNECTED')
