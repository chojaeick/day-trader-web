#!/usr/bin/env python3
from pathlib import Path
import re, shutil, subprocess, time, urllib.request

print('=== V238 RELAX KOREA MTF FOR SIMULATION + REENABLE ===')
print('SCOPE=KIWOOM_MOCK_ONLY REAL_ACCOUNT_CHANGE=NONE USA_CHANGE=NONE')
print('GOAL=AVOID_ZERO_TRADES_WHILE_KEEPING_1M_ANTI_CHASE_AND_5M_HARD_BEAR_REJECT')

ENV=Path('/home/ubuntu/day-trader-api/.env')
ENG=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
if not ENV.exists() or not ENG.exists():
    raise SystemExit('V238_ABORT runtime files missing')

# stop mock first
env=ENV.read_text()
if re.search(r'(?m)^WILLIAMS_KIWOOM_MOCK_AUTO=',env):
    env=re.sub(r'(?m)^WILLIAMS_KIWOOM_MOCK_AUTO=.*$','WILLIAMS_KIWOOM_MOCK_AUTO=0',env)
else:
    env+='\nWILLIAMS_KIWOOM_MOCK_AUTO=0\n'
ENV.write_text(env)
subprocess.run(['sudo','systemctl','restart','day-trader-api'])
time.sleep(2)
print('PHASE1_MOCK_OFF=', 'WILLIAMS_KIWOOM_MOCK_AUTO=0' in ENV.read_text())

s=ENG.read_text()
backup=ENG.with_name('v4_engine.py.bak_v238')
shutil.copy2(ENG,backup)
print('BACKUP=',backup)

old="""                        _five_ok=bool(_r5n is not None and _r5n>=45.0 and ((_m5n>=_s5n) or (_h5n is not None and _h5p is not None and _h5n>=_h5p)) and _cl5>=_ema5)"""
new="""                        # V238_MTF_SCORE_GATE: simulation calibration.
                        # 5m is a direction filter, not an exact timing trigger.
                        _five_score=sum([
                            bool(_r5n is not None and _r5n>=45.0),
                            bool((_m5n>=_s5n) or (_h5n is not None and _h5p is not None and _h5n>=_h5p)),
                            bool(_cl5 is not None and _ema5 is not None and _cl5>=_ema5),
                        ])
                        _five_hard_bear=bool(
                            _r5n is not None and _r5n<40.0 and
                            _h5n is not None and _h5p is not None and _h5n<_h5p and
                            _cl5 is not None and _ema5 is not None and _cl5<_ema5
                        )
                        # Strong 1m momentum (3/3 improving) needs only one 5m support.
                        # Normal 1m momentum (2/3) still needs two 5m supports.
                        _five_ok=bool((not _five_hard_bear) and ((_improve>=3 and _five_score>=1) or (_improve>=2 and _five_score>=2)))"""

if 'V238_MTF_SCORE_GATE' in s:
    patched=0
    print('PATCH_ALREADY_PRESENT=YES')
elif old in s:
    s=s.replace(old,new,1)
    ENG.write_text(s)
    patched=1
else:
    raise SystemExit('V238_ABORT strict 5m gate anchor not found')
print('PATCH_MTF_SCORE_GATE=',patched)

py='/home/ubuntu/day-trader-api/venv/bin/python3'
rc=subprocess.run([py,'-m','py_compile',str(ENG)]).returncode
print('PY_COMPILE_RC=',rc)
text=ENG.read_text()
static={
 'V233_STRUCT5_PRICE_GUARD':'V233_STRUCT5_LIVE_PRICE_GUARD' in text,
 'V234_MTF_GUARD':'V234_MTF_ENTRY_GUARD' in text,
 'V235_TELEMETRY':'V235_MTF_TELEMETRY' in text,
 'V237_PASS_BRIDGE':'V237_MTF_PASS_BRIDGE' in text,
 'V238_SCORE_GATE':'V238_MTF_SCORE_GATE' in text,
 'BUY_PATH':'b.buy_market(sym,qty)' in text,
}
print('STATIC_CHECKS=',static)
if rc!=0 or not all(static.values()):
    print('V238_PASS=False')
    print('KOREA_MOCK_AUTO_RUNNING=NO')
    raise SystemExit(2)

# re-enable mock only after all checks pass
env=ENV.read_text()
env=re.sub(r'(?m)^WILLIAMS_KIWOOM_MOCK_AUTO=.*$','WILLIAMS_KIWOOM_MOCK_AUTO=1',env)
ENV.write_text(env)
rr=subprocess.run(['sudo','systemctl','restart','day-trader-api']).returncode
print('RESTART_RC=',rr)

ready=False
for i in range(1,31):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2) as r:
            body=r.read(300).decode('utf-8','replace')
            print('READY',i,'HTTP=',r.status,body[:160])
            if r.status==200:
                ready=True; break
    except Exception as e:
        print('READY',i,'FAIL=',type(e).__name__)
    time.sleep(2)

active=subprocess.run(['systemctl','is-active','day-trader-api'],capture_output=True,text=True).stdout.strip()
print('SERVICE_ACTIVE=',active)
print('MOCK_AUTO_ENV_ON=', 'WILLIAMS_KIWOOM_MOCK_AUTO=1' in ENV.read_text())
print('V238_PASS=', bool(ready and active=='active'))
print('KOREA_MOCK_AUTO_RUNNING=', 'YES' if ready and active=='active' else 'NO')
print('ENTRY_POLICY=1M_3OF3+5M_1OF3_OR_1M_2OF3+5M_2OF3; HARD_BEAR_5M_BLOCK')
print('BASELINE=ONLY_TRADES_AFTER_V238_COUNT_AS_RELAXED_MTF_SIMULATION')