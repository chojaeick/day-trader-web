#!/usr/bin/env python3
from pathlib import Path

P = Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
s = P.read_text(encoding='utf-8')

IMPORT_LINE = 'from live_server.kiwoom_mock_broker import KiwoomMockBroker\n'
if IMPORT_LINE not in s:
    lines = s.splitlines(True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith('from __future__ import '):
            insert_at = i + 1
        elif line.startswith('import ') or line.startswith('from '):
            insert_at = i + 1
        elif line.strip() and insert_at:
            break
    lines.insert(insert_at, IMPORT_LINE)
    s = ''.join(lines)

METHOD = '''\n    def _williams_kiwoom_mock_step(self, market, row):\n        if market != "KOREA" or not isinstance(row, dict):\n            return row\n        try:\n            import os\n            if os.getenv("KIWOOM_MOCK_AUTO_ENABLED", "0").lower() not in ("1", "true", "yes", "on"):\n                return row\n            symbol = str(row.get("symbol") or row.get("code") or "").replace("A", "").zfill(6)\n            if not symbol or symbol == "000000":\n                return row\n            broker = KiwoomMockBroker()\n            qty = int(os.getenv("KIWOOM_MOCK_AUTO_QTY", "1"))\n            if row.get("williams_entry") or row.get("williams_signal_entry"):\n                r = broker.buy_market(symbol, qty)\n                row["kiwoom_mock_order"] = {"side":"BUY","ord_no":r.get("ord_no"),"return_msg":r.get("return_msg")}\n            elif row.get("williams_exit_ready"):\n                r = broker.sell_market(symbol, qty)\n                row["kiwoom_mock_order"] = {"side":"SELL","ord_no":r.get("ord_no"),"return_msg":r.get("return_msg")}\n        except Exception as e:\n            row["kiwoom_mock_order_error"] = f"{type(e).__name__}: {e}"\n        return row\n'''

if 'def _williams_kiwoom_mock_step(self, market, row):' not in s:
    anchor = '    def _finalize(self, market, rows):\n'
    if anchor not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: _finalize')
    s = s.replace(anchor, METHOD + '\n' + anchor, 1)

# Wire into _finalize row loop after paper step if present, otherwise before return.
if 'self._williams_kiwoom_mock_step(market, r)' not in s:
    paper_call = 'self._paper_williams_step(market,r)'
    paper_call_sp = 'self._paper_williams_step(market, r)'
    if paper_call in s:
        s = s.replace(paper_call, paper_call + '\n            self._williams_kiwoom_mock_step(market, r)', 1)
    elif paper_call_sp in s:
        s = s.replace(paper_call_sp, paper_call_sp + '\n            self._williams_kiwoom_mock_step(market, r)', 1)
    else:
        marker = '        return rows\n'
        idx = s.find('    def _finalize(self, market, rows):\n')
        if idx < 0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: finalize body')
        j = s.find(marker, idx)
        if j < 0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: finalize return')
        inject = '        for r in rows:\n            self._williams_kiwoom_mock_step(market, r)\n'
        s = s[:j] + inject + s[j:]

bak = P.with_suffix('.py.v102b.bak')
if not bak.exists():
    bak.write_text(P.read_text(encoding='utf-8'), encoding='utf-8')
P.write_text(s, encoding='utf-8')
print('PATCHED', P)
print('ADDED=_williams_kiwoom_mock_step')
print('WIRED=_finalize')
print('AUTO_GATE=KIWOOM_MOCK_AUTO_ENABLED')
print('ORDER_GATE=KIWOOM_MOCK_ORDER_ENABLE')
print('DEFAULT_QTY=1')
print('REAL_BROKER_ORDERS=NO')
