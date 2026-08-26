#!/usr/bin/env python3
"""V140 wire frozen Williams evaluator into USA paper-only path.

Safety:
- runtime file patch only
- USA only
- paper engine only
- no real broker calls
- Korea Williams mock path untouched
- backup before patch
"""
from pathlib import Path
import shutil, re

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v140')
S=P.read_text()
if not B.exists(): shutil.copy2(P,B)

IMPORT='''\n# V140 frozen USA Williams paper evaluator (V139 replay-equivalent)\ntry:\n    from . import williams_usa_frozen as _wuf\nexcept Exception:\n    _wuf=None\n'''
if 'V140 frozen USA Williams paper evaluator' not in S:
    marker='from .analytics import ticks_to_bars, multi_timeframe_signal\n'
    S=S.replace(marker,marker+IMPORT,1)

METHOD=r'''
    def _v140_usa_frozen_williams_eval(self, row):
        """USA-only frozen Williams paper evaluator. No broker authority."""
        if _wuf is None or str((row or {}).get('market','')).upper()!='USA':
            return {'entry':False,'exit':False,'reason':'NOT_USA_OR_MODULE_MISSING'}
        try:
            ctx=(row or {}).get('williams_frozen_ctx') or {}
            out={'entry':False,'exit':False,'reason':'NO_CTX'}
            if ctx.get('entry_args'):
                e=_wuf.entry_signal(**ctx['entry_args'])
                out.update({'entry':bool(e.get('signal')),'entry_eval':e,'reason':'ENTRY_EVAL'})
            if ctx.get('exit_args'):
                x=_wuf.exit_signal(**ctx['exit_args'])
                out.update({'exit':bool(x.get('exit')),'exit_eval':x,'reason':'EXIT_EVAL'})
            return out
        except Exception as e:
            return {'entry':False,'exit':False,'reason':'ERROR','error':str(e)}
'''
if 'def _v140_usa_frozen_williams_eval' not in S:
    anchor='    def _paper_williams_step(self, market, row):\n'
    if anchor not in S: raise SystemExit('ANCHOR_NOT_FOUND:_paper_williams_step')
    S=S.replace(anchor,METHOD+'\n'+anchor,1)

# Inject USA-only frozen decision ahead of legacy paper Williams handling.
needle='''    def _paper_williams_step(self, market, row):\n        """Paper-only Williams execution bridge. Never calls a real broker."""\n'''
if needle in S and 'V140_USA_FROZEN_PAPER_ONLY' not in S:
    repl=needle+'''        # V140_USA_FROZEN_PAPER_ONLY: isolated from Korea mock/structure logic.\n        if str(market).upper()=='USA':\n            ev=self._v140_usa_frozen_williams_eval(row)\n            row['williams_frozen_eval']=ev\n            # Telemetry only until V141 context adapter proves live feature parity.\n            # Do NOT place/enter/exit here yet.\n            return None\n'''
    S=S.replace(needle,repl,1)

P.write_text(S)
print('=== V140 WIRE FROZEN USA PAPER PATH ===')
print('PATCHED',P)
print('BACKUP',B)
print('USA_ONLY=YES')
print('PAPER_ONLY=YES')
print('BROKER_CALLS_ADDED=NONE')
print('KOREA_PATH_TOUCHED=NO')
print('ORDER_AUTHORITY=NONE_TELEMETRY_ONLY')
print('NEXT=V141_BUILD_LIVE_FEATURE_CONTEXT_ADAPTER_AND_PARITY_AUDIT')
