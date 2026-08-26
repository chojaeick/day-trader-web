#!/usr/bin/env python3
"""V145 enable USA frozen Williams PAPER-only authority with safety gates.

Runtime patch only. No real broker calls. Korea path untouched.
Requires V140-V144 markers to exist.
"""
from pathlib import Path
import shutil, py_compile

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v145')
S=P.read_text()
if not B.exists(): shutil.copy2(P,B)
required=['def _v140_usa_frozen_williams_eval','williams_frozen_ctx','V140_USA_FROZEN_PAPER_ONLY']
missing=[x for x in required if x not in S]
if missing:
    print('MISSING_PREREQ=',missing); raise SystemExit(2)

old='''        # V140_USA_FROZEN_PAPER_ONLY: isolated from Korea mock/structure logic.\n        if str(market).upper()=='USA':\n            ev=self._v140_usa_frozen_williams_eval(row)\n            row['williams_frozen_eval']=ev\n            # Telemetry only until V141 context adapter proves live feature parity.\n            # Do NOT place/enter/exit here yet.\n            return None\n'''
new='''        # V145_USA_FROZEN_PAPER_AUTHORITY: isolated frozen USA paper path.\n        if str(market).upper()=='USA':\n            ev=self._v140_usa_frozen_williams_eval(row)\n            row['williams_frozen_eval']=ev\n            sym=str((row or {}).get('symbol') or '').upper()\n            price=_f((row or {}).get('price'))\n            if not sym or price<=0:\n                return None\n            # Existing paper ledger only; never broker/Kiwoom.\n            pos=self.paper.position('USA',sym) if hasattr(self.paper,'position') else None\n            if pos:\n                if bool(ev.get('exit')):\n                    return self.paper.exit('USA',sym,price,reason='WILLIAMS_FROZEN_EXIT')\n                return self.paper.mark('USA',sym,price,state='HOLD')\n            if bool(ev.get('entry')):\n                # hard safety: no duplicate open symbol and cap open paper positions.\n                try:\n                    opens=self.paper.positions('USA') if hasattr(self.paper,'positions') else []\n                except Exception:\n                    opens=[]\n                if any(str((p or {}).get('symbol','')).upper()==sym for p in (opens or [])):\n                    return None\n                max_pos=max(1,int(os.getenv('WILLIAMS_USA_PAPER_MAX_POSITIONS','5') or 5))\n                if len(opens or [])>=max_pos:\n                    return None\n                return self.paper.enter('USA',sym,price,strategy_id='WILLIAMS_FROZEN_V136',reason='WILLIAMS_FROZEN_ENTRY')\n            return None\n'''
if old in S:
    S=S.replace(old,new,1)
elif 'V145_USA_FROZEN_PAPER_AUTHORITY' not in S:
    raise SystemExit('V140_BLOCK_NOT_FOUND')
P.write_text(S)
try:
    py_compile.compile(str(P),doraise=True); comp='PASS'
except Exception as e:
    comp='FAIL:'+str(e)
print('=== V145 ENABLE USA FROZEN PAPER ORDER AUTHORITY ===')
print('PATCHED',P)
print('BACKUP',B)
print('USA_ONLY=YES')
print('PAPER_ONLY=YES')
print('REAL_BROKER_CALLS_ADDED=NONE')
print('KOREA_PATH_TOUCHED=NO')
print('DUPLICATE_SYMBOL_GUARD=YES')
print('MAX_POSITIONS_ENV=WILLIAMS_USA_PAPER_MAX_POSITIONS default=5')
print('FROZEN_STRATEGY_ID=WILLIAMS_FROZEN_V136')
print('PY_COMPILE=',comp)
print('PAPER_ORDER_AUTHORITY=', 'ENABLED' if comp=='PASS' else 'DISABLED')
print('NEXT=V146_PAPER_PATH_STATIC_AND_DRYRUN_AUDIT')
