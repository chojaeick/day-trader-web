#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

old='''    def refresh_korea_tracker(self,korea):\n\n        syms=self.tracked_symbols('KOREA')\n\n        fmap={r['symbol']:r for r in self.finder['KOREA']['rows']}\n\n        pmap={p['symbol']:p for p in self.store.positions('KOREA')}\n\n        pulse={\n\n            str(r.get('symbol') or ''):r\n\n            for r in (korea.intraday_pulse.get('rows') or [])\n\n        }\n\n        rows=[]\n'''

new='''    def refresh_korea_tracker(self,korea):\n\n        # V108: separate Williams ENTRY discovery from legacy/open-position tracking.\n        # Paper-entry candidates must not be displaced by existing portfolio positions.\n        finder_rows=list(self.finder['KOREA'].get('rows') or [])\n        fmap={str(r.get('symbol') or ''):r for r in finder_rows if str(r.get('symbol') or '')}\n\n        pmap={p['symbol']:p for p in self.store.positions('KOREA')}\n\n        pulse_rows=list(korea.intraday_pulse.get('rows') or [])\n        pulse={\n            str(r.get('symbol') or ''):r\n            for r in pulse_rows\n            if str(r.get('symbol') or '')\n        }\n\n        # Candidate pool: Finder rank first, then live pulse strength/score.\n        # Explicitly exclude legacy/open positions so Williams ENTRY scans fresh symbols.\n        pos_syms=set(str(x) for x in pmap.keys())\n        candidate_syms=[]\n\n        for r in sorted(\n            finder_rows,\n            key=lambda x:(\n                int(x.get('rank')) if str(x.get('rank') or '').isdigit() else 999999,\n                -_f(x.get('finder_score'))\n            )\n        ):\n            sym=str(r.get('symbol') or '')\n            if sym and sym not in pos_syms and sym not in candidate_syms:\n                candidate_syms.append(sym)\n\n        for r in sorted(\n            pulse_rows,\n            key=lambda x:max(\n                _f(x.get('live_score')),\n                _f(x.get('strength_composite')),\n                _f(x.get('change_pct')),\n                _f(x.get('rate'))\n            ),\n            reverse=True\n        ):\n            sym=str(r.get('symbol') or '')\n            if sym and sym not in pos_syms and sym not in candidate_syms:\n                candidate_syms.append(sym)\n\n        # Keep the live Williams scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        rows=[]\n'''

if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: refresh_korea_tracker header block')
s=s.replace(old,new,1)

# Tag rows so UI/status can distinguish fresh entry candidates from legacy holdings.
needle="""                'market':'KOREA',\n\n                'symbol':sym,\n"""
repl="""                'market':'KOREA',\n\n                'symbol':sym,\n\n                'williams_candidate':True,\n                'tracker_role':'WILLIAMS_ENTRY_CANDIDATE',\n"""
if needle not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: korea row tag anchor')
s=s.replace(needle,repl,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('KOREA_TRACKER_ROLE=WILLIAMS_ENTRY_CANDIDATES_ONLY')
print('LEGACY_OPEN_POSITIONS_EXCLUDED_FROM_ENTRY_POOL=YES')
print('CANDIDATE_SOURCE=FINDER_THEN_INTRADAY_PULSE')
print('MAX_WILLIAMS_CANDIDATES=8')
print('MOCK_ORDER_PATH=UNCHANGED')
print('REAL_BROKER_FALLBACK=NO')
