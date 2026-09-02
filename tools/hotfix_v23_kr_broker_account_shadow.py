from pathlib import Path

bp=Path('live_server/kiwoom_mock_broker.py')
vp=Path('live_server/v4_engine.py')
bs=bp.read_text()
vs=vp.read_text()

# 1) Capture every successful account response inside KiwoomMockBroker.request_account
# without depending on the exact runtime syntax of the caller.
if 'self._last_account_response = d' not in bs:
    old='''        return d\n'''
    idx=bs.find('def request_account')
    if idx < 0:
        raise SystemExit('request_account NOT FOUND')
    end=bs.find('\n    def ', idx+5)
    if end < 0: end=len(bs)
    block=bs[idx:end]
    pos=block.rfind(old)
    if pos < 0:
        raise SystemExit('request_account return d NOT FOUND')
    abspos=idx+pos
    bs=bs[:abspos]+'''        if isinstance(d, dict):\n            self._last_account_response = d\n        return d\n'''+bs[abspos+len(old):]
    bp.write_text(bs)

# 2) In the V23 entry sizing path, fall back to the broker's last successful
# account response when the engine-level cache is absent. No extra API call.
needle="""                _bal=getattr(self,'_williams_mock_account_cache',None)\n                _cache_age=_time.monotonic()-float(getattr(self,'_williams_mock_account_cache_mono',0.0) or 0.0)\n"""
if "_last_account_response" not in vs:
    if needle not in vs:
        raise SystemExit('V23 account cache block NOT FOUND')
    repl="""                _bal=getattr(self,'_williams_mock_account_cache',None)\n                _cache_age=_time.monotonic()-float(getattr(self,'_williams_mock_account_cache_mono',0.0) or 0.0)\n                if not isinstance(_bal,dict):\n                    _broker_bal=getattr(b,'_last_account_response',None)\n                    if isinstance(_broker_bal,dict):\n                        _bal=_broker_bal\n                        _cache_age=0.0\n                        self._williams_mock_account_cache=_broker_bal\n                        self._williams_mock_account_cache_mono=_time.monotonic()\n"""
    vs=vs.replace(needle,repl,1)
    vp.write_text(vs)

print('V23 KR BROKER ACCOUNT SHADOW CONNECTED')
