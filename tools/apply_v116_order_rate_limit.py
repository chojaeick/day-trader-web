#!/usr/bin/env python3
"""Apply DAY TRADER V116 mock order API rate limiter to runtime broker.

Runtime target: /home/ubuntu/day-trader-api
Scope:
- serialize all mock BUY/SELL orders process-wide
- enforce configurable minimum spacing between order API calls
- default spacing: 5.0 seconds
- do not change signal/entry/exit logic
- do not start/restart services
"""
from pathlib import Path
import py_compile
import shutil

ROOT = Path('/home/ubuntu/day-trader-api')
BROKER = ROOT / 'live_server' / 'kiwoom_mock_broker.py'


def fail(msg):
    raise SystemExit(f'V116_ABORT: {msg}')


def main():
    print('TARGET_ROOT', ROOT)
    if not BROKER.exists():
        fail(f'missing {BROKER}')

    backup = BROKER.with_name(BROKER.name + '.bak_v116')
    if not backup.exists():
        shutil.copy2(BROKER, backup)
        print('BACKUP', backup)

    s = BROKER.read_text()

    if 'import time\n' not in s:
        if 'import requests\n' in s:
            s = s.replace('import requests\n', 'import requests\nimport time\n', 1)
        else:
            fail('requests import anchor not found')

    if '_order_lock = threading.Lock()' not in s:
        anchor = '    _token_lock = threading.Lock()\n'
        if anchor not in s:
            fail('V115 token lock anchor not found')
        s = s.replace(anchor, anchor + '    _order_lock = threading.Lock()\n    _last_order_mono: float = 0.0\n', 1)

    if 'def _order_guard(self)' not in s:
        marker = '    def _ensure_order_enabled(self) -> None:\n'
        if marker not in s:
            fail('_ensure_order_enabled marker not found')
        helper = '''    def _order_guard(self):\n        cls = type(self)\n        interval = float(os.getenv("KIWOOM_MOCK_ORDER_MIN_INTERVAL_SEC", "5.0") or 5.0)\n        if interval < 0:\n            interval = 0.0\n        with cls._order_lock:\n            now = time.monotonic()\n            wait = interval - (now - cls._last_order_mono)\n            if wait > 0:\n                time.sleep(wait)\n            cls._last_order_mono = time.monotonic()\n\n'''
        s = s.replace(marker, helper + marker, 1)

    buy_old = '''    def buy_market(self, symbol: str, qty: int) -> dict[str, Any]:\n        self._ensure_order_enabled()\n        if int(qty) <= 0:\n            raise ValueError("qty must be > 0")\n        return self._post(\n'''
    buy_new = '''    def buy_market(self, symbol: str, qty: int) -> dict[str, Any]:\n        self._ensure_order_enabled()\n        if int(qty) <= 0:\n            raise ValueError("qty must be > 0")\n        self._order_guard()\n        return self._post(\n'''
    if 'self._order_guard()\n        return self._post(' not in s[s.find('    def buy_market'):s.find('    def sell_market')]:
        if buy_old not in s:
            fail('buy_market anchor not found')
        s = s.replace(buy_old, buy_new, 1)

    sell_start = s.find('    def sell_market')
    if sell_start < 0:
        fail('sell_market not found')
    sell_tail = s[sell_start:]
    if 'self._order_guard()\n        return self._post(' not in sell_tail:
        sell_old = '''    def sell_market(self, symbol: str, qty: int) -> dict[str, Any]:\n        self._ensure_order_enabled()\n        if int(qty) <= 0:\n            raise ValueError("qty must be > 0")\n        return self._post(\n'''
        sell_new = '''    def sell_market(self, symbol: str, qty: int) -> dict[str, Any]:\n        self._ensure_order_enabled()\n        if int(qty) <= 0:\n            raise ValueError("qty must be > 0")\n        self._order_guard()\n        return self._post(\n'''
        if sell_old not in s:
            fail('sell_market anchor not found')
        s = s.replace(sell_old, sell_new, 1)

    BROKER.write_text(s)
    py_compile.compile(str(BROKER), doraise=True)
    print('BROKER_PATCHED')
    print('V116_PATCH_OK')
    print('ORDER_MIN_INTERVAL_DEFAULT_SEC=5.0')
    print('SERVICE_NOT_STARTED')


if __name__ == '__main__':
    main()
