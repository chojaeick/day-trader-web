#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/kiwoom_mock_broker.py')
s=P.read_text()
orig=s

# V114: _williams_mock_auto_step creates a broker adapter repeatedly.
# Without shared token reuse, every call requests /oauth2/token and quickly hits 429.

old='''class KiwoomMockBroker:\n    """Kiwoom mock-investment broker adapter.\n'''
new='''class KiwoomMockBroker:\n    _shared_token: str | None = None\n\n    """Kiwoom mock-investment broker adapter.\n'''
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: KiwoomMockBroker class header')
s=s.replace(old,new,1)

old2='''    def __init__(self, config: MockBrokerConfig | None = None):\n        self.cfg = config or MockBrokerConfig.from_env()\n        self.token: str | None = None\n'''
new2='''    def __init__(self, config: MockBrokerConfig | None = None):\n        self.cfg = config or MockBrokerConfig.from_env()\n        self.token: str | None = type(self)._shared_token\n'''
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: broker init token')
s=s.replace(old2,new2,1)

old3='''        self.token = d["token"]\n        return self.token\n'''
new3='''        self.token = d["token"]\n        type(self)._shared_token = self.token\n        return self.token\n'''
if old3 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: token assignment')
s=s.replace(old3,new3,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/kiwoom_mock_broker.py')
print('MOCK_OAUTH_TOKEN_CACHE=PROCESS_SHARED')
print('EXPECTED_TOKEN_CALLS=ONE_PER_PROCESS_START')
print('429_TOKEN_PRESSURE=REDUCED')
print('ORDER_LOGIC_CHANGED=NO')
print('REAL_BROKER_FALLBACK=NO')
