from pathlib import Path
import subprocess, sys, time, urllib.request, json, re

ENV=Path('/home/ubuntu/day-trader-api/.env')
ENGINE=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')

print('=== V234C REENABLE KOREA MOCK AFTER V233+V234 GUARDS ===')
print('REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')

s=ENGINE.read_text()
checks={
 'V233_STRUCT5_PRICE_GUARD': 'V233_STRUCT5_PRICE_SYNC_GUARD' in s,
 'V234_MTF_GUARD': 'V234_MTF_ENTRY_GUARD' in s,
 'MOCK_BUY_PATH': 'b.buy_market(sym,qty)' in s,
 'MOCK_SELL_PATH': 'b.sell_market(sym,qty)' in s,
}
print('STATIC_CHECKS=',checks)
if not all(checks.values()):
    print('V234C_PASS= False')
    print('KOREA_MOCK_AUTO_RUNNING=NO')
    sys.exit(2)

env=ENV.read_text() if ENV.exists() else ''
key='WILLIAMS_KIWOOM_MOCK_AUTO'
if re.search(rf'(?m)^\s*{re.escape(key)}\s*=.*$', env):
    env=re.sub(rf'(?m)^\s*{re.escape(key)}\s*=.*$', f'{key}=1', env)
else:
    env += ('\n' if env and not env.endswith('\n') else '') + f'{key}=1\n'
ENV.write_text(env)
print('MOCK_AUTO_ENV_ON=', True)

rc=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rc)

ready=False
runtime=None
for i in range(1,16):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/runtime-mode',timeout=3) as r:
            runtime=json.loads(r.read().decode())
            print('READY',i,'HTTP=',r.status,'MODE=',runtime.get('mode'))
            ready=True
            break
    except Exception as e:
        print('READY',i,'FAIL=',type(e).__name__)
        time.sleep(2)
print('API_READY=',ready,'RUNTIME=',runtime)
active=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True).stdout.strip()
print('SERVICE_ACTIVE=',active)
passed=all(checks.values()) and rc==0 and ready and active=='active'
print('V234C_PASS=',passed)
print('KOREA_MOCK_AUTO_RUNNING=', 'YES' if passed else 'UNKNOWN')
print('BASELINE=ONLY_TRADES_AFTER_V234C_COUNT_AS_NEW_MTF_GUARD_RUN')
