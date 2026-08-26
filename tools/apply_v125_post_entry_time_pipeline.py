#!/usr/bin/env python3
"""Apply DAY TRADER V125 post-entry time pipeline repair.

Root cause:
- V118 filters STRUCT0 bars to timestamps strictly after BUY using the 'time' column.
- _korea_shadow_gate() built bars_raw with only open/high/low/close, dropping time.
- Therefore V118 silently skipped filtering and reused the full 240-bar day history,
  allowing stale pre-entry support to trigger ordinary exits after the 5-minute hold.

V125 behavior:
- preserve time in gate['bars_raw'] used by _williams_structure_from_gate().
- do not change ENTRY rules, support math, 5-minute minimum hold, V123 hard stop,
  V120 holdings-first tracker policy, or any order sizing/rate-limit logic.
- compile-check only; never starts/restarts services.
"""
from pathlib import Path
import py_compile, shutil

ROOT=Path('/home/ubuntu/day-trader-api')
ENGINE=ROOT/'live_server'/'v4_engine.py'

def fail(msg): raise SystemExit('V125_ABORT: '+msg)

def main():
    print('TARGET_ROOT',ROOT)
    if not ENGINE.exists(): fail('missing engine')
    bak=ENGINE.with_name(ENGINE.name+'.bak_v125')
    if not bak.exists():
        shutil.copy2(ENGINE,bak)
        print('BACKUP',bak)
    s=ENGINE.read_text()
    marker='# V125: preserve time so V118 can filter strictly post-entry bars.'
    if marker in s:
        py_compile.compile(str(ENGINE),doraise=True)
        print('ENGINE_ALREADY_V125'); print('V125_PATCH_OK'); print('SERVICE_NOT_STARTED'); return

    old="                'bars_raw':b[['open','high','low','close']].tail(240).to_dict('records'),\n"
    new=("                # V125: preserve time so V118 can filter strictly post-entry bars.\n"
         "                'bars_raw':b[[c for c in ('time','open','high','low','close') if c in b.columns]].tail(240).to_dict('records'),\n")
    if old not in s: fail('bars_raw anchor not found')
    s=s.replace(old,new,1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE),doraise=True)
    print('ENGINE_PATCHED')
    print('V125_PATCH_OK')
    print('BARS_RAW_TIME_PRESERVED=YES')
    print('V118_POST_ENTRY_FILTER=ENABLED_BY_DATA_PIPELINE')
    print('ENTRY_LOGIC=UNCHANGED')
    print('HARD_STOP_V123=UNCHANGED')
    print('SERVICE_NOT_STARTED')

if __name__=='__main__': main()
