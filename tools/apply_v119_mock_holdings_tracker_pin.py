#!/usr/bin/env python3
"""Apply DAY TRADER V119 mock-holding tracker pin to the live runtime tree.

Observed failure:
- Kiwoom mock account held 015760 after a Williams BUY.
- KOREA tracker later rotated to five other symbols, so the held symbol no longer
  reached _williams_mock_auto_step(). That could disable V118 structural exit and
  the independent -1.5% emergency stop for the live mock holding.

V119 behavior:
- after V115 account sync, remember all positive-quantity Kiwoom mock holdings.
- KOREA tracker always appends any held mock symbol missing from the normal tracker.
- held rows are refreshed through the same normal KR row builder, not synthetic prices.
- tracker may exceed the normal discovery/top-5 count while positions are open; safety
  monitoring takes precedence over the display candidate limit.
- V118 post-entry structure, V116 throttling, max BUY positions and hard stop unchanged.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile, shutil

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server'/'v4_engine.py'

def fail(x): raise SystemExit('V119_ABORT: '+x)

def main():
    print('TARGET_ROOT',ROOT)
    if not ENGINE.exists(): fail('missing engine')
    bak=ENGINE.with_name(ENGINE.name+'.bak_v119')
    if not bak.exists(): shutil.copy2(ENGINE,bak); print('BACKUP',bak)
    s=ENGINE.read_text()
    marker='# V119: pin actual Kiwoom mock holdings into KOREA tracker.'
    if marker in s:
        py_compile.compile(str(ENGINE),doraise=True)
        print('ENGINE_ALREADY_V119'); print('V119_PATCH_OK'); print('SERVICE_NOT_STARTED'); return

    # Find the tracker row loop call site. Current runtime calls _williams_mock_auto_step(r)
    # once for each completed KOREA tracker row. We add held-symbol rows immediately before
    # that loop's final tracker assignment by extending the symbol source, using the same
    # existing row-generation path.
    # Runtime layout is intentionally discovered by stable anchors to avoid replacing repo copies.

    # 1. Add helper returning currently restored/open mock symbols from engine state.
    anchor='    def _williams_mock_auto_step(self, row):\n'
    if anchor not in s: fail('auto-step anchor not found')
    helper='''    def _williams_mock_held_symbols(self):\n        """V119: symbols whose Kiwoom mock lifecycle state is currently open."""\n        out=[]\n        for k,st in list(self._last.items()):\n            if not (isinstance(k,tuple) and len(k)==2 and k[0]=="WILLIAMS_MOCK"):\n                continue\n            if isinstance(st,dict) and st.get("in_pos"):\n                sym=str(k[1]).replace("A","").zfill(6)\n                if sym not in out: out.append(sym)\n        return out\n\n'''
    s=s.replace(anchor,helper+anchor,1)

    # 2. refresh_korea_tracker builds its symbol list from finder rows. Inject held symbols
    # into that list before row construction. Accept the known compact runtime spellings.
    candidates=[
        "        syms=[str(x.get('symbol')) for x in frows[:5]]\n",
        '        syms=[str(x.get("symbol")) for x in frows[:5]]\n',
        "        symbols=[str(x.get('symbol')) for x in frows[:5]]\n",
        '        symbols=[str(x.get("symbol")) for x in frows[:5]]\n',
    ]
    found=None
    for a in candidates:
        if a in s: found=a; break
    if found is None:
        # broader stable fallback: locate refresh_korea_tracker and first [:5] list assignment.
        start=s.find('    def refresh_korea_tracker(')
        if start<0: fail('refresh_korea_tracker not found')
        end=s.find('\n    def ',start+5)
        if end<0: end=len(s)
        block=s[start:end]
        lines=block.splitlines(True)
        off=start
        for line in lines:
            if '[:5]' in line and ('symbol' in line) and ('=' in line) and ('[' in line):
                found=line; break
            off+=len(line)
        if found is None: fail('tracker symbol-list anchor not found')

    var=found.split('=',1)[0].strip()
    indent=found[:len(found)-len(found.lstrip())]
    inject=found+indent+marker+'\n'+indent+'for _hs in self._williams_mock_held_symbols():\n'+indent+'    if _hs not in '+var+': '+var+'.append(_hs)\n'
    s=s.replace(found,inject,1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE),doraise=True)
    print('ENGINE_PATCHED')
    print('V119_PATCH_OK')
    print('TRACKER_POLICY=DISCOVERY_PLUS_ALL_OPEN_MOCK_HOLDINGS')
    print('HELD_SYMBOLS_MAY_EXCEED_TOP5=YES')
    print('V118_POST_ENTRY_EXIT=UNCHANGED')
    print('HARD_STOP=-1.5%_UNCHANGED')
    print('SERVICE_NOT_STARTED')

if __name__=='__main__': main()
