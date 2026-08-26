#!/usr/bin/env python3
"""Apply DAY TRADER V118 post-entry Williams structure repair.

Runtime target: /home/ubuntu/day-trader-api

Root cause fixed:
- STRUCT0 support was calculated from the whole trading day, including bars before BUY.
- A pre-entry support could therefore sit above the new entry price and keep
  EXIT_READY=True before and after BUY.
- V117 prevented the stale exit from firing, but its ENTRY+EXIT conflict guard could
  also suppress valid fresh breakouts indefinitely.

V118 behavior (mock Williams bridge):
- ENTRY is no longer blocked merely because the whole-day shadow EXIT is True.
- after BUY, save the KST entry bar time for that position.
- while a mock position is open, calculate Williams STRUCT0 only from completed
  1-minute bars strictly AFTER the entry minute.
- pre-entry support is therefore discarded for the new position.
- post-entry support still uses the original causal 2-right-bar swing-low rule and
  ratchets upward exactly as before.
- ordinary structural SELL still requires >=5 minutes hold and post-entry EXIT_READY.
- -1.5% emergency hard stop remains independent and immediate.
- V115 account sync, V116 order throttling, quantity/max-position rules remain intact.
- never starts/restarts systemd services.
"""
from pathlib import Path
import py_compile
import shutil

ROOT = Path('/home/ubuntu/day-trader-api')
ENGINE = ROOT / 'live_server' / 'v4_engine.py'


def fail(msg):
    raise SystemExit(f'V118_ABORT: {msg}')


def main():
    print('TARGET_ROOT', ROOT)
    if not ENGINE.exists():
        fail(f'missing {ENGINE}')

    backup = ENGINE.with_name(ENGINE.name + '.bak_v118')
    if not backup.exists():
        shutil.copy2(ENGINE, backup)
        print('BACKUP', backup)

    s = ENGINE.read_text()

    if '# V118: only bars strictly after the BUY minute belong to this position.' in s:
        py_compile.compile(str(ENGINE), doraise=True)
        print('ENGINE_ALREADY_V118')
        print('V118_PATCH_OK')
        print('SERVICE_NOT_STARTED')
        return

    # 1) STRUCT0 accepts an optional position epoch and filters to bars after entry minute.
    sig_old = '    def _williams_structure_state(self,b1,entry_price=None):\n'
    sig_new = '    def _williams_structure_state(self,b1,entry_price=None,start_time=None):\n'
    if sig_old not in s:
        fail('_williams_structure_state signature anchor not found')
    s = s.replace(sig_old, sig_new, 1)

    filter_old = '''            b=b1.copy().reset_index(drop=True)\n            for col in ('open','high','low','close'):\n                b[col]=pd.to_numeric(b[col],errors='coerce')\n            b=b.dropna(subset=['high','low','close']).reset_index(drop=True)\n            out['bars']=len(b)\n            if len(b)<7:return out\n\n            support=None; updates=0\n'''
    filter_new = '''            b=b1.copy().reset_index(drop=True)\n            for col in ('open','high','low','close'):\n                b[col]=pd.to_numeric(b[col],errors='coerce')\n\n            # V118: only bars strictly after the BUY minute belong to this position.\n            # This discards all pre-entry STRUCT0 support and also excludes the partial\n            # entry minute. Kiwoom minute timestamps are YYYYMMDDHHMMSS-like strings.\n            if start_time and 'time' in b.columns:\n                start_digits=''.join(ch for ch in str(start_time) if ch.isdigit())\n                if len(start_digits)>=12:\n                    b['_v118_time']=b['time'].astype(str).str.replace(r'\\D','',regex=True)\n                    b=b[b['_v118_time'].str.len()>=12]\n                    b=b[b['_v118_time'].str[:12] > start_digits[:12]]\n                    b=b.sort_values('_v118_time').reset_index(drop=True)\n\n            b=b.dropna(subset=['high','low','close']).reset_index(drop=True)\n            out['bars']=len(b)\n            out['structure_start_time']=start_time\n            if len(b)<7:return out\n\n            support=None; updates=0\n'''
    if filter_old not in s:
        fail('STRUCT0 body anchor not found')
    s = s.replace(filter_old, filter_new, 1)

    # 2) Gate adapter forwards the position epoch to STRUCT0.
    gate_sig_old = '    def _williams_structure_from_gate(self,gate,entry_price=None):\n'
    gate_sig_new = '    def _williams_structure_from_gate(self,gate,entry_price=None,start_time=None):\n'
    if gate_sig_old not in s:
        fail('_williams_structure_from_gate signature anchor not found')
    s = s.replace(gate_sig_old, gate_sig_new, 1)

    state_call_old = '            out=self._williams_structure_state(b1,entry_price=entry_price)\n'
    state_call_new = '            out=self._williams_structure_state(b1,entry_price=entry_price,start_time=start_time)\n'
    if state_call_old not in s:
        fail('STRUCT0 gate call anchor not found')
    s = s.replace(state_call_old, state_call_new, 1)

    # 3) Row construction uses the mock position epoch only while that symbol is held.
    row_old = '            williams_struct=self._williams_structure_from_gate(gate,entry_price=_wentry)\n'
    row_new = '''            _wmock_st=self._last.get(("WILLIAMS_MOCK",str(sym).zfill(6)),{})\n            _wmock_start=(\n                _wmock_st.get("entered_bar_time")\n                if isinstance(_wmock_st,dict) and _wmock_st.get("in_pos")\n                else None\n            )\n            williams_struct=self._williams_structure_from_gate(\n                gate,entry_price=_wentry,start_time=_wmock_start\n            )\n'''
    if row_old not in s:
        fail('row Williams structure call anchor not found')
    s = s.replace(row_old, row_new, 1)

    # 4) Account-restored positions receive an entry-bar timestamp. If the latest BUY
    # time is unavailable, use current KST time conservatively so stale support cannot fire.
    sync_old = '''            self._last[("WILLIAMS_MOCK", sym)] = {\n                "in_pos": True,\n                "qty": qty,\n                "entry_price": avg,\n                "entered_ts": entered_ts,\n                "exit_armed": False,\n                "synced_from_account": True,\n            }\n'''
    sync_new = '''            entered_bar_time=(now.strftime('%Y%m%d') + tm) if tm and len(tm)>=6 else now.strftime('%Y%m%d%H%M%S')\n            self._last[("WILLIAMS_MOCK", sym)] = {\n                "in_pos": True,\n                "qty": qty,\n                "entry_price": avg,\n                "entered_ts": entered_ts,\n                "entered_bar_time": entered_bar_time,\n                "synced_from_account": True,\n            }\n'''
    if sync_old not in s:
        fail('V117 account-sync state anchor not found')
    s = s.replace(sync_old, sync_new, 1)

    # 5) Remove V117 ENTRY+EXIT conflict block. Whole-day EXIT is pre-entry telemetry;
    # after BUY the next tracker cycle switches to the position-specific post-entry structure.
    conflict = '''                # V117: ENTRY and EXIT_READY on the same row is contradictory.\n                # Do not open a position whose pre-existing exit state is already active.\n                if exit_ready:\n                    import logging as _logging\n                    _logging.warning(\n                        "WILLIAMS_MOCK_BUY_BLOCKED_CONFLICT sym=%s price=%s entry=%s exit_ready=%s",\n                        sym,_f(row.get("price")),entry,exit_ready\n                    )\n                    return\n\n'''
    if conflict not in s:
        fail('V117 conflict block not found')
    s = s.replace(conflict, '''                # V118: pre-entry whole-day EXIT telemetry does not veto a fresh ENTRY.\n                # Once BUY is accepted, subsequent rows use only post-entry structure.\n''', 1)

    # 6) Fresh BUY stores entry KST time; V117 exit_armed is no longer needed.
    buy_old = '''                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                    # V117: stale pre-entry EXIT must not be reused by a fresh position.\n                    # A post-entry EXIT_READY=False observation must occur before\n                    # structural exits can become armed. Emergency hard stop is separate.\n                    "exit_armed":False,\n                }\n'''
    buy_new = '''                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                    "entered_bar_time":_dt.now(_WILLIAMS_KST).strftime('%Y%m%d%H%M%S'),\n                }\n'''
    if buy_old not in s:
        fail('V117 BUY state anchor not found')
    s = s.replace(buy_old, buy_new, 1)

    # 7) Replace V117 false->true arming with direct post-entry structural exit.
    exit_old = '''                # V117: emergency -1.5% stop stays independent and immediate.\n                # Ordinary structural exit must be FRESH for this position:\n                #   BUY -> wait until EXIT_READY becomes False -> arm -> later True -> exit.\n                # This prevents a pre-entry EXIT=True from being executed merely because\n                # the 5-minute minimum hold timer expired.\n                if not hard_stop:\n                    exit_armed=bool(st.get("exit_armed"))\n                    if not exit_armed:\n                        if not exit_ready:\n                            st["exit_armed"]=True\n                            self._last[key]=st\n                            import logging as _logging\n                            _logging.warning(\n                                "WILLIAMS_MOCK_EXIT_ARMED sym=%s hold_sec=%.1f price=%s",\n                                sym,hold_sec,price\n                            )\n                        return\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n'''
    exit_new = '''                # V118: row EXIT_READY is now computed from bars strictly after\n                # this position's BUY minute. Pre-entry support cannot trigger this exit.\n                # Emergency -1.5% hard stop remains independent and immediate.\n                if not hard_stop:\n                    if not exit_ready:\n                        return\n                    if hold_sec < 300.0:\n                        return\n\n                r=b.sell_market(sym,qty)\n'''
    if exit_old not in s:
        fail('V117 exit-arming block not found')
    s = s.replace(exit_old, exit_new, 1)

    ENGINE.write_text(s)
    py_compile.compile(str(ENGINE), doraise=True)
    print('ENGINE_PATCHED')
    print('V118_PATCH_OK')
    print('EXIT_STRUCTURE=POST_ENTRY_1M_ONLY')
    print('ENTRY_CONFLICT_BLOCK=REMOVED')
    print('MIN_HOLD_SEC=300_UNCHANGED')
    print('HARD_STOP=-1.5%_UNCHANGED')
    print('SERVICE_NOT_STARTED')


if __name__ == '__main__':
    main()
