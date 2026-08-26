#!/usr/bin/env python3
from pathlib import Path

P=Path('live_server/v4_engine.py')
s=P.read_text()
orig=s

# V109: V108 may source candidates from intraday_pulse when KOREA Finder rows are empty.
# Williams V23 still requires a <=20 attention rank, so provide an explicit bounded
# paper-validation candidate rank from the already sorted pulse candidate pool.
# This does NOT change the threshold; it changes the rank source only when Finder rank is absent.

old="""        # Keep the live Williams scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        rows=[]\n"""
new="""        # Keep the live Williams scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        # Paper-validation attention rank. Finder rank remains authoritative when present;\n        # otherwise use the order of the bounded live candidate pool (1..8).\n        pulse_candidate_rank={sym:i+1 for i,sym in enumerate(syms)}\n\n        rows=[]\n"""
if old not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: V108 bounded candidate block')
s=s.replace(old,new,1)

old2="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n            williams_entry_eval=self._williams_entry_from_gate(sym,gate,finder_rank=f.get('rank'))\n"""
new2="""            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n            _finder_rank=f.get('rank')\n            _williams_rank=_finder_rank if _finder_rank is not None else pulse_candidate_rank.get(sym)\n            _williams_rank_source='FINDER' if _finder_rank is not None else 'LIVE_CANDIDATE_POOL'\n            williams_entry_eval=self._williams_entry_from_gate(sym,gate,finder_rank=_williams_rank)\n"""
if old2 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: Williams entry evaluator call')
s=s.replace(old2,new2,1)

# Make the effective rank visible in tracker output instead of leaving pulse candidates as None.
old3="""                'finder_rank':f.get('rank'),\n"""
new3="""                'finder_rank':_williams_rank,\n                'williams_rank_source':_williams_rank_source,\n"""
if old3 not in s:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: finder_rank row field')
s=s.replace(old3,new3,1)

if s==orig:
    raise SystemExit('NO_CHANGE')
P.write_text(s)
print('PATCHED live_server/v4_engine.py')
print('FINDER_RANK_PRIORITY=FINDER')
print('FALLBACK_RANK_SOURCE=LIVE_CANDIDATE_POOL_1_TO_8')
print('WILLIAMS_RANK_THRESHOLD=UNCHANGED_LE20')
print('EXTRA_BROKER_API_CALLS=0')
print('PAPER_VALIDATION_ONLY=YES')
print('REAL_BROKER_FALLBACK=NO')
