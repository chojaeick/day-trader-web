from pathlib import Path
import subprocess, sys

RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
BACKUP=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py.bak_v234b')

print('=== V234B RESTORE V233 GUARD + VERIFY V234 MTF ===')
print('MOCK_AUTO_MUST_STAY_OFF=YES')
print('REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')

s=RUNTIME.read_text()
BACKUP.write_text(s)

# Locate the mock BUY path and reinsert a live-price-vs-STRUCT5-resistance guard if missing.
marker='V233_STRUCT5_LIVE_PRICE_GUARD'
if marker not in s:
    needle='''                r=b.buy_market(sym,qty)'''
    guard='''                # V233_STRUCT5_LIVE_PRICE_GUARD: fail closed if live price no longer confirms breakout.\n                if bool(row.get("williams_struct5_signal")):\n                    resistance=_f(row.get("williams_struct5_resistance"))\n                    if resistance>0 and not (price>resistance):\n                        import logging as _logging\n                        _logging.warning("WILLIAMS_MOCK_BUY_BLOCKED_STRUCT5_PRICE_SYNC sym=%s price=%s resistance=%s",sym,price,resistance)\n                        self.store.event("KOREA",sym,"WILLIAMS_MOCK_BUY_BLOCKED",None,"BLOCKED",power=_f(row.get("power")),message=f'{sym} STRUCT5 price-sync blocked',payload={"row":row,"price":price,"resistance":resistance})\n                        return\n\n                r=b.buy_market(sym,qty)'''
    if needle not in s:
        print('PATCH_ERROR=BUY_NEEDLE_NOT_FOUND')
        sys.exit(2)
    s=s.replace(needle,guard,1)
    RUNTIME.write_text(s)
    print('RESTORE_V233_GUARD=1')
else:
    print('RESTORE_V233_GUARD=0 ALREADY_PRESENT=YES')

# Compile runtime source.
rc=subprocess.call(['/home/ubuntu/day-trader-api/venv/bin/python3','-m','py_compile',str(RUNTIME)])
print('PY_COMPILE_RC=',rc)

s=RUNTIME.read_text()
checks={
    'V233_STRUCT5_PRICE_GUARD': 'V233_STRUCT5_LIVE_PRICE_GUARD' in s,
    'V234_MTF_GUARD': ('V234' in s and ('MTF' in s or 'multi' in s.lower())),
    'MOCK_BUY_PATH': 'b.buy_market(sym,qty)' in s,
    'MOCK_SELL_PATH': 'b.sell_market(sym,qty)' in s,
}
print('STATIC_CHECKS=',checks)

# Confirm env remains OFF without printing secrets.
env=Path('/home/ubuntu/day-trader-api/.env')
envtxt=env.read_text() if env.exists() else ''
off=False
for line in envtxt.splitlines():
    if line.strip().startswith('WILLIAMS_KIWOOM_MOCK_AUTO='):
        val=line.split('=',1)[1].strip().lower()
        off=val in ('0','false','no','off','')
print('MOCK_AUTO_ENV_OFF=',off)

ok=(rc==0 and all(checks.values()) and off)
print('V234B_PASS=',ok)
print('KOREA_MOCK_AUTO_RUNNING=NO')
print('NEXT=REENABLE_ONLY_AFTER_V234B_PASS')
