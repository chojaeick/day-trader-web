from pathlib import Path

p=Path('live_server/v4_engine.py')
s=p.read_text()

old='''            from live_server.kiwoom_mock_broker import KiwoomMockBroker\n            b=KiwoomMockBroker()\n            if not b.cfg.order_enable:\n'''
new='''            from live_server.kiwoom_mock_broker import KiwoomMockBroker\n            b=getattr(self, '_williams_mock_broker', None)\n            if b is None:\n                b=KiwoomMockBroker()\n                self._williams_mock_broker=b\n            if not b.cfg.order_enable:\n'''

if old not in s:
    raise SystemExit('BROKER TARGET NOT FOUND - NOTHING CHANGED')

s=s.replace(old,new,1)
p.write_text(s)
print('V23 KR MOCK BROKER SINGLETON CONNECTED')
