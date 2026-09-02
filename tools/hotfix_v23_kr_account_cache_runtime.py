from pathlib import Path
import re

p=Path('live_server/v4_engine.py')
s=p.read_text()

start=s.find('    def _williams_mock_sync_account(')
if start<0:
    raise SystemExit('SYNC FUNCTION NOT FOUND - NOTHING CHANGED')
end=s.find('\n    def ', start+5)
if end<0:
    end=len(s)
block=s[start:end]

if '_williams_mock_account_cache=' in block:
    print('V23 KR ACCOUNT CACHE RUNTIME ALREADY CONNECTED')
    raise SystemExit(0)

# Find the actual kt00004 account-response assignment used by the live runtime.
m=re.search(r'(?m)^(\s*)([A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\.request_account\([^\n]*kt00004[^\n]*\)\s*$', block)
if not m:
    raise SystemExit('KT00004 SYNC ASSIGNMENT NOT FOUND - NOTHING CHANGED')
indent,var=m.group(1),m.group(2)
line=m.group(0)
insert=(line+'\n'+indent+f'self._williams_mock_account_cache={var}'+'\n'+indent+"self._williams_mock_account_cache_mono=__import__('time').monotonic()"+'\n'+indent+"__import__('logging').warning('WILLIAMS_MOCK_ACCOUNT_CACHE_READY type=%s', type(self._williams_mock_account_cache).__name__)")
block2=block.replace(line,insert,1)
s=s[:start]+block2+s[end:]
p.write_text(s)
print(f'V23 KR ACCOUNT CACHE RUNTIME CONNECTED var={var}')
