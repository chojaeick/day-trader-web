#!/usr/bin/env python3
"""Apply DAY TRADER V119 mock-holding tracker pin to the live runtime tree.

Observed failure:
- Kiwoom mock account held 015760 after a Williams BUY.
- KOREA tracker rotated to other candidates, so the held symbol no longer reached
  _williams_mock_auto_step(). That could disable V118 exit and -1.5% hard-stop monitoring.

Actual runtime layout verified before this revision:
- refresh_korea_tracker builds candidate_syms from Finder + pulse
- then bounds discovery with: syms=candidate_syms[:8]

V119 behavior:
- expose currently-open WILLIAMS_MOCK lifecycle symbols from self._last
- append those held symbols AFTER the bounded discovery list, before row construction
- therefore open mock positions cannot be displaced by Finder/pulse rotation
- safety monitoring may make tracker rows exceed the normal discovery bound while held
- V118 post-entry structure, V116 throttling, max BUY positions and hard stop unchanged
- never starts/restarts systemd services
"""
from pathlib import Path
import py_compile
import shutil

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server'/'v4_engine.py'


def fail(msg):
    raise SystemExit('V119_ABORT: '+msg)


def main():
    print('TARGET_ROOT',ROOT)
    if not ENGINE.exists():
        fail('missing engine')

    bak=ENGINE.with_name(ENGINE.name+'.bak_v119')
    if not bak.exists():
        shutil.copy2(ENGINE,bak)
        print('BACKUP',bak)

    s=ENGINE.read_text()
    marker='# V119: pin actual Kiwoom mock holdings into KOREA tracker.'
    if marker in s:
        py_compile.compile(str(ENGINE),doraise=True)
        print('ENGINE_ALREADY_V119')
        print('V119_PATCH_OK')
        print('SERVICE_NOT_STARTED')
        return

    # 1) Helper derives actual open mock lifecycle symbols. V115 account sync fills these
    # states on the first tracker row after process start; subsequent refreshes pin them.
    auto_anchor='    def _williams_mock_auto_step(self, row):\n'
    if auto_anchor not in s:
        fail('auto-step anchor not found')
    helper='''    def _williams_mock_held_symbols(self):\n        """V119: currently open Kiwoom mock lifecycle symbols."""\n        out=[]\n        for k,st in list(self._last.items()):\n            if not (isinstance(k,tuple) and len(k)==2 and k[0]=="WILLIAMS_MOCK"):\n                continue\n            if not isinstance(st,dict) or not st.get("in_pos"):\n                continue\n            sym=str(k[1] or '').replace('A','').zfill(6)\n            if sym and sym not in out:\n                out.append(sym)\n        return out\n\n'''
    s=s.replace(auto_anchor,helper+auto_anchor,1)

    # 2) Actual verified V108 runtime anchor.
    syms_anchor='''        # Keep the live Williams scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        # Paper-validation attention rank. Finder rank remains authoritative when present;\n'''
    syms_new='''        # Keep the live Williams discovery scan bounded for Kiwoom rate limits.\n        syms=candidate_syms[:8]\n\n        # V119: pin actual Kiwoom mock holdings into KOREA tracker.\n        # Discovery remains bounded, but an already-open mock position is safety-critical:\n        # it must continue reaching V118 structural exit and the -1.5% hard stop even if\n        # Finder/pulse ranking rotates it out of candidate_syms.\n        for _held_sym in self._williams_mock_held_symbols():\n            if _held_sym not in syms:\n                syms.append(_held_sym)\n\n        # Paper-validation attention rank. Finder rank remains authoritative when present;\n'''
    if syms_anchor not in s:
        fail('verified candidate_syms[:8] anchor not found')
    s=s.replace(syms_anchor,syms_new,1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE),doraise=True)
    print('ENGINE_PATCHED')
    print('V119_PATCH_OK')
    print('TRACKER_POLICY=BOUNDED_DISCOVERY_PLUS_ALL_OPEN_MOCK_HOLDINGS')
    print('HELD_SYMBOLS_MAY_EXCEED_DISCOVERY_BOUND=YES')
    print('V118_POST_ENTRY_EXIT=UNCHANGED')
    print('HARD_STOP=-1.5%_UNCHANGED')
    print('SERVICE_NOT_STARTED')


if __name__=='__main__':
    main()
