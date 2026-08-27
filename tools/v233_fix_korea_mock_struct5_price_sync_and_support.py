#!/usr/bin/env python3
from pathlib import Path
import subprocess, time, urllib.request, json, shutil

TARGET=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
BACKUP=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py.bak_v233')
print('=== V233 FIX KOREA MOCK STRUCT5 PRICE SYNC + INVALID SUPPORT ===')
print('SCOPE=KIWOOM_MOCK_ONLY REAL_ACCOUNT_CHANGE=NONE WILLIAMS_FORMULA_CHANGE=NONE')
print('PURPOSE=BLOCK_STRUCT5_ORDER_IF_LIVE_PRICE_NO_LONGER_ABOVE_RESISTANCE_AND_PREVENT_PREENTRY_INVALID_SUPPORT_IN_PAPER_LEDGER')

s=TARGET.read_text()
shutil.copy2(TARGET,BACKUP)
print('BACKUP=',BACKUP)

old1="""            wentry=bool(row.get('williams_entry') or row.get('williams_signal_entry'))\n            if wentry and row.get('session')=='REGULAR':\n                return self.paper.enter(market,sym,price,strategy_id='WILLIAMS_STRUCT0',reason='WILLIAMS_ENTRY',support=row.get('williams_support'))\n"""
new1="""            wentry=bool(row.get('williams_entry') or row.get('williams_signal_entry'))\n            if wentry and row.get('session')=='REGULAR':\n                # V233: pre-entry STRUCT0 support is diagnostic only.  A long position\n                # must never inherit a support at/above the entry price.  Post-entry\n                # structure will establish/ratchet support from subsequent bars.\n                _pre_support=_f(row.get('williams_support'))\n                _entry_support=_pre_support if (0 < _pre_support < price) else None\n                return self.paper.enter(market,sym,price,strategy_id='WILLIAMS_STRUCT0',reason='WILLIAMS_ENTRY',support=_entry_support)\n"""
if old1 not in s:
    raise SystemExit('V233_ABORT paper entry anchor not found')
s=s.replace(old1,new1,1)
print('PATCH_PAPER_SUPPORT=1')

old2="""                price=_f(row.get(\"price\"))\n                if price<=0:\n                    return\n\n                # Reserve capital for positions opened by this bridge in the current process.\n"""
new2="""                price=_f(row.get(\"price\"))\n                if price<=0:\n                    return\n\n                # V233: STRUCT5 is a fresh 5-bar resistance breakout.  Never submit a\n                # mock BUY if the live order price has already fallen back to/below the\n                # resistance that generated the signal.  This prevents stale/misaligned\n                # chart-vs-quote snapshots such as signal resistance 6400 with order 6360.\n                if bool(row.get('williams_struct5_signal')):\n                    _s5_res=_f(row.get('williams_struct5_resistance'))\n                    if _s5_res > 0 and price <= _s5_res:\n                        import logging as _logging\n                        _logging.warning(\"WILLIAMS_MOCK_ENTRY_BLOCKED_STRUCT5_PRICE sym=%s price=%s resistance=%s\",sym,price,_s5_res)\n                        self.store.event(\"KOREA\",sym,\"WILLIAMS_MOCK_ENTRY_BLOCKED\",None,\"BLOCKED\",power=_f(row.get(\"power\")),message=f'{sym} STRUCT5 live price no longer above resistance',payload={\"row\":row,\"price\":price,\"resistance\":_s5_res})\n                        return\n\n                # Reserve capital for positions opened by this bridge in the current process.\n"""
if old2 not in s:
    raise SystemExit('V233_ABORT broker entry anchor not found')
s=s.replace(old2,new2,1)
print('PATCH_STRUCT5_LIVE_PRICE_GUARD=1')

TARGET.write_text(s)

rc=subprocess.run(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(TARGET)]).returncode
print('PY_COMPILE_RC=',rc)
if rc!=0:
    shutil.copy2(BACKUP,TARGET)
    raise SystemExit('V233_ABORT compile failed; backup restored')

# Static verification
s2=TARGET.read_text()
checks={
 'STRUCT5_PRICE_GUARD': 'WILLIAMS_MOCK_ENTRY_BLOCKED_STRUCT5_PRICE' in s2,
 'INVALID_PRE_SUPPORT_BLOCK': "_entry_support=_pre_support if (0 < _pre_support < price) else None" in s2,
 'MOCK_BUY_PATH_STILL_PRESENT': 'r=b.buy_market(sym,qty)' in s2,
 'MOCK_SELL_PATH_STILL_PRESENT': 'r=b.sell_market(sym,qty)' in s2,
}
print('STATIC_CHECKS=',checks)
if not all(checks.values()):
    shutil.copy2(BACKUP,TARGET)
    raise SystemExit('V233_ABORT static verification failed; backup restored')

rr=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rr)
ready=False
runtime=None
for i in range(1,25):
    time.sleep(2)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=2) as r:
            runtime=json.loads(r.read().decode())
            print('READY',i,'HTTP=',r.status,'MODE=',runtime.get('mode'))
            if r.status==200:
                ready=True
                break
    except Exception as e:
        print('READY',i,'FAIL=',type(e).__name__)
print('API_READY=',ready,'RUNTIME=',runtime)

p=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True)
print('SERVICE_ACTIVE=',p.stdout.strip())

print('V233_PASS=', bool(ready and p.stdout.strip()=='active'))
print('KOREA_MOCK_RESTARTED=YES' if ready else 'KOREA_MOCK_RESTARTED=NO')
print('NEXT=CONFIRM_KIWOOM_MOCK_OPEN_HOLDINGS_THEN_OBSERVE_ONLY_NEW_V233_TRADES')
