#!/usr/bin/env python3
"""Apply DAY TRADER V117 fresh-exit epoch safety repair to runtime engine.

Runtime target: /home/ubuntu/day-trader-api

Observed failure fixed by this patch:
- 041190 had EXIT=True before BUY, remained EXIT=True through the whole hold,
  then sold almost immediately after the 5-minute minimum hold expired.

V117 scope (mock Williams bridge only):
- block a new BUY when ENTRY and EXIT_READY are simultaneously true
- after a successful BUY, structural exit starts DISARMED
- structural exit becomes armed only after EXIT_READY is observed False post-entry
- after arming, a later fresh EXIT_READY=True may trigger ordinary exit
- -1.5% emergency hard stop remains independent and immediately active
- account-restored positions also start structural exit DISARMED
- no change to quantity, max positions, OAuth, or V116 order rate limiting
- never starts/restarts systemd services
"""
from pathlib import Path
import py_compile
import shutil

ROOT = Path('/home/ubuntu/day-trader-api')
ENGINE = ROOT / 'live_server' / 'v4_engine.py'


def fail(msg):
    raise SystemExit(f'V117_ABORT: {msg}')


def main():
    print('TARGET_ROOT', ROOT)
    if not ENGINE.exists():
        fail(f'missing {ENGINE}')

    backup = ENGINE.with_name(ENGINE.name + '.bak_v117')
    if not backup.exists():
        shutil.copy2(ENGINE, backup)
        print('BACKUP', backup)

    s = ENGINE.read_text()

    if '# V117: stale pre-entry EXIT must not be reused by a fresh position.' in s:
        py_compile.compile(str(ENGINE), doraise=True)
        print('ENGINE_ALREADY_V117')
        print('V117_PATCH_OK')
        print('SERVICE_NOT_STARTED')
        return

    # 1) Restored broker positions must not inherit the current row's stale EXIT.
    sync_old = '''                "entered_ts": entered_ts,\n                "synced_from_account": True,\n'''
    sync_new = '''                "entered_ts": entered_ts,\n                "exit_armed": False,\n                "synced_from_account": True,\n'''
    if sync_old not in s:
        fail('account-sync state anchor not found; V115 layout changed')
    s = s.replace(sync_old, sync_new, 1)

    # 2) Block contradictory ENTRY+EXIT rows before any capital/order work.
    entry_old = '''            if entry and not in_pos:\n                # Retry guard: avoid hammering Kiwoom if a pending breakout survives multiple refreshes.\n'''
    entry_new = '''            if entry and not in_pos:\n                # V117: ENTRY and EXIT_READY on the same row is contradictory.\n                # Do not open a position whose pre-existing exit state is already active.\n                if exit_ready:\n                    import logging as _logging\n                    _logging.warning(\n                        "WILLIAMS_MOCK_BUY_BLOCKED_CONFLICT sym=%s price=%s entry=%s exit_ready=%s",\n                        sym,_f(row.get("price")),entry,exit_ready\n                    )\n                    return\n\n                # Retry guard: avoid hammering Kiwoom if a pending breakout survives multiple refreshes.\n'''
    if entry_old not in s:
        fail('entry branch anchor not found; V115/V116 layout changed')
    s = s.replace(entry_old, entry_new, 1)

    # 3) Successful fresh BUY starts a new structural-exit epoch, disarmed.
    buy_state_old = '''                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                }\n'''
    buy_state_new = '''                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                    # V117: stale pre-entry EXIT must not be reused by a fresh position.\n                    # A post-entry EXIT_READY=False observation must occur before\n                    # structural exits can become armed. Emergency hard stop is separate.\n                    "exit_armed":False,\n                }\n'''
    if buy_state_old not in s:
        fail('BUY state anchor not found')
    s = s.replace(buy_state_old, buy_state_new, 1)

    # 4) Replace V115 normal-exit gate with false->true fresh-exit epoch logic.
    exit_old = '''                # V115: emergency -1.5% stop is independent of EXIT_READY.\n                # Ordinary structural exits still require EXIT_READY and >=5m hold.\n                if not hard_stop:\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n'''
    exit_new = '''                # V117: emergency -1.5% stop stays independent and immediate.\n                # Ordinary structural exit must be FRESH for this position:\n                #   BUY -> wait until EXIT_READY becomes False -> arm -> later True -> exit.\n                # This prevents a pre-entry EXIT=True from being executed merely because\n                # the 5-minute minimum hold timer expired.\n                if not hard_stop:\n                    exit_armed=bool(st.get("exit_armed"))\n                    if not exit_armed:\n                        if not exit_ready:\n                            st["exit_armed"]=True\n                            self._last[key]=st\n                            import logging as _logging\n                            _logging.warning(\n                                "WILLIAMS_MOCK_EXIT_ARMED sym=%s hold_sec=%.1f price=%s",\n                                sym,hold_sec,price\n                            )\n                        return\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n'''
    if exit_old not in s:
        fail('V115 exit gate anchor not found')
    s = s.replace(exit_old, exit_new, 1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE), doraise=True)
    print('ENGINE_PATCHED')
    print('V117_PATCH_OK')
    print('RULE=BLOCK_ENTRY_IF_EXIT_TRUE + REQUIRE_POST_ENTRY_EXIT_FALSE_THEN_TRUE')
    print('HARD_STOP=-1.5%_UNCHANGED')
    print('SERVICE_NOT_STARTED')


if __name__ == '__main__':
    main()
