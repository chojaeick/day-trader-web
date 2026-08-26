#!/usr/bin/env python3
from pathlib import Path

p=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
s=p.read_text()

if 'def _williams_mock_auto_step(' in s:
    print('ALREADY_PATCHED: _williams_mock_auto_step')
    raise SystemExit(0)

# Add imports near the top, independent of exact existing import layout.
insert='from live_server.kiwoom_mock_broker import KiwoomMockBroker\n'
if insert not in s:
    lines=s.splitlines(True)
    idx=0
    while idx < len(lines) and (lines[idx].startswith('#!') or lines[idx].startswith('# -*-') or lines[idx].strip()=='' or lines[idx].startswith('from __future__')):
        idx += 1
    lines.insert(idx, insert)
    s=''.join(lines)

# Find a stable class method anchor from V94.
anchor='    def _williams_structure_shadow('
pos=s.find(anchor)
if pos<0:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: _williams_structure_shadow')

method='''    def _williams_mock_auto_step(self, market, row):\n        \"\"\"Optional Kiwoom MOCK bridge. No live-broker fallback.\"\"\"\n        import os, time\n        if str(market).upper() != 'KOREA':\n            return row\n        enabled = os.getenv('KIWOOM_MOCK_AUTO_ENABLED','0').lower() in ('1','true','yes','on')\n        if not enabled:\n            row['williams_mock_auto'] = 'OFF'\n            return row\n        if os.getenv('KIWOOM_MOCK_ORDER_ENABLE','0').lower() not in ('1','true','yes','on'):\n            row['williams_mock_auto'] = 'ORDER_DISABLED'\n            return row\n        sym = str(row.get('symbol') or row.get('code') or '').replace('A','').zfill(6)\n        if not sym.isdigit() or len(sym) != 6:\n            row['williams_mock_auto'] = 'BAD_SYMBOL'\n            return row\n        entry = bool(row.get('williams_entry') or row.get('williams_signal_entry'))\n        exit_ready = bool(row.get('williams_exit_ready'))\n        state = getattr(self, '_williams_mock_auto_state', None)\n        if state is None:\n            state = self._williams_mock_auto_state = {}\n        st = state.setdefault(sym, {'position': False, 'last_order_ts': 0.0, 'buy_order_no': None, 'sell_order_no': None})\n        now=time.time()\n        if now - float(st.get('last_order_ts') or 0) < 2.0:\n            row['williams_mock_auto'] = 'RATE_GUARD'\n            return row\n        try:\n            b=KiwoomMockBroker()\n            if entry and not st['position']:\n                r=b.buy_market(sym, 1)\n                st.update(position=True,last_order_ts=now,buy_order_no=r.get('ord_no') or r.get('order_no'))\n                row['williams_mock_auto']='BUY_SENT'\n                row['williams_mock_order_no']=st['buy_order_no']\n            elif exit_ready and st['position']:\n                r=b.sell_market(sym, 1)\n                st.update(position=False,last_order_ts=now,sell_order_no=r.get('ord_no') or r.get('order_no'))\n                row['williams_mock_auto']='SELL_SENT'\n                row['williams_mock_order_no']=st['sell_order_no']\n            else:\n                row['williams_mock_auto']='HOLD' if st['position'] else 'IDLE'\n        except Exception as e:\n            row['williams_mock_auto']='ERROR'\n            row['williams_mock_error']=f'{type(e).__name__}: {e}'[:240]\n        return row\n\n'''
s=s[:pos]+method+s[pos:]

# Wire at the stable Korea row enrichment point from V94: after williams_exit_ready is assigned.
needle="            row['williams_exit_ready'] = williams_struct.get('exit_ready')\n"
if needle not in s:
    needle='            row["williams_exit_ready"] = williams_struct.get("exit_ready")\n'
if needle not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: williams_exit_ready row assignment')
replacement=needle+"            row = self._williams_mock_auto_step('KOREA', row)\n"
s=s.replace(needle,replacement,1)

p.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('ADDED=_williams_mock_auto_step')
print('WIRED=KOREA_ROW_AFTER_STRUCT0')
print('DEFAULT_AUTO=OFF')
print('ORDER_REQUIRES=KIWOOM_MOCK_AUTO_ENABLED=1 + KIWOOM_MOCK_ORDER_ENABLE=1')
print('ORDER_QTY=1')
print('REAL_BROKER_FALLBACK=NO')
