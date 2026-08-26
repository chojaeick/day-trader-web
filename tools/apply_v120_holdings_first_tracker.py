#!/usr/bin/env python3
"""Apply DAY TRADER V120 holdings-first KOREA tracker safety repair.

Runtime target: /home/ubuntu/day-trader-api

Root cause fixed:
- V119 appended open WILLIAMS_MOCK holdings after candidate_syms[:8].
- _finalize() later applies rows[:TRACK_LIMIT] (TRACK_LIMIT=5 in current runtime).
- therefore safety-critical held symbols could still be truncated out before
  _williams_mock_auto_step(), disabling V118 structural exits and -1.5% hard stops.

V120 behavior:
- open mock holdings are placed FIRST in refresh_korea_tracker syms.
- remaining slots/candidates follow in normal Finder/pulse order with duplicates removed.
- row generation remains bounded to 8 symbols for Kiwoom rate control.
- because max mock holdings is 5, every held symbol survives _finalize rows[:5].
- no change to V118 post-entry structure, V116 order throttle, quantity/max positions,
  5-minute ordinary hold, or -1.5% emergency hard stop.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile
import shutil

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server'/'v4_engine.py'


def fail(msg):
    raise SystemExit('V120_ABORT: '+msg)


def main():
    print('TARGET_ROOT',ROOT)
    if not ENGINE.exists():
        fail('missing engine')

    bak=ENGINE.with_name(ENGINE.name+'.bak_v120')
    if not bak.exists():
        shutil.copy2(ENGINE,bak)
        print('BACKUP',bak)

    s=ENGINE.read_text()
    marker='# V120: holdings FIRST so _finalize rows[:TRACK_LIMIT] cannot drop them.'
    if marker in s:
        py_compile.compile(str(ENGINE),doraise=True)
        print('ENGINE_ALREADY_V120')
        print('V120_PATCH_OK')
        print('SERVICE_NOT_STARTED')
        return

    old='''        # Keep the live Williams discovery scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        # V119: pin actual Kiwoom mock holdings into KOREA tracker.\n        # Discovery remains bounded, but an already-open mock position is safety-critical:\n        # it must continue reaching V118 structural exit and the -1.5% hard stop even if\n        # Finder/pulse ranking rotates it out of candidate_syms.\n        for _held_sym in self._williams_mock_held_symbols():\n            if _held_sym not in syms:\n                syms.append(_held_sym)\n\n        # Paper-validation attention rank. Finder rank remains authoritative when present;\n'''

    new='''        # V120: holdings FIRST so _finalize rows[:TRACK_LIMIT] cannot drop them.\n        # Current mock max positions is 5, matching the final tracker safety window.\n        _held_syms=self._williams_mock_held_symbols()\n        syms=list(_held_syms)\n        for _cand_sym in candidate_syms:\n            if _cand_sym not in syms:\n                syms.append(_cand_sym)\n            if len(syms)>=8:\n                break\n\n        # Paper-validation attention rank. Finder rank remains authoritative when present;\n'''

    if old not in s:
        fail('V119 tracker pin block not found')
    s=s.replace(old,new,1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE),doraise=True)
    print('ENGINE_PATCHED')
    print('V120_PATCH_OK')
    print('TRACKER_ORDER=OPEN_MOCK_HOLDINGS_FIRST_THEN_DISCOVERY')
    print('ROW_BUILD_BOUND=8')
    print('FINAL_TRACK_LIMIT_SAFETY=OPEN_HOLDINGS_SURVIVE_FIRST_5')
    print('V118_POST_ENTRY_EXIT=UNCHANGED')
    print('HARD_STOP=-1.5%_UNCHANGED')
    print('SERVICE_NOT_STARTED')


if __name__=='__main__':
    main()
