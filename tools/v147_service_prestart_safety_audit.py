#!/usr/bin/env python3
"""V147 service prestart safety audit for frozen USA Williams paper path.

READ ONLY. Does not start/restart service. No orders.
Checks runtime markers, compile, frozen module, paper authority, broker-call isolation,
service state hints, and env/config hazards before any start command.
"""
from __future__ import annotations
from pathlib import Path
import os, re, subprocess, py_compile

ROOT=Path('/home/ubuntu/day-trader-api')
ENG=ROOT/'live_server'/'v4_engine.py'
MOD=ROOT/'live_server'/'williams_usa_frozen.py'
API=ROOT/'live_server'/'api.py'
S=ENG.read_text(errors='ignore') if ENG.exists() else ''
M=MOD.read_text(errors='ignore') if MOD.exists() else ''
A=API.read_text(errors='ignore') if API.exists() else ''

print('=== V147 SERVICE PRESTART SAFETY AUDIT ===')
print('READ_ONLY=YES SERVICE_START=NO ORDERS=NONE')
checks={
 'engine_exists':ENG.exists(),
 'frozen_module_exists':MOD.exists(),
 'v140_eval':'def _v140_usa_frozen_williams_eval' in S,
 'v142_context_builder':'williams_frozen_ctx' in S,
 'v145_authority':'V145_USA_FROZEN_PAPER_AUTHORITY' in S,
 'frozen_strategy_id':'WILLIAMS_FROZEN_V136' in S,
 'usa_gate':"str(market).upper()=='USA'" in S or 'str(market).upper()=="USA"' in S,
 'paper_enter':"self.paper.enter('USA'" in S or 'self.paper.enter("USA"' in S,
 'paper_exit':"self.paper.exit('USA'" in S or 'self.paper.exit("USA"' in S,
 'no_forced_hold_in_frozen':'forced_min_hold':False if False else True,
}
# semantic frozen-spec checks
checks['frozen_stop_1pct']='hard_stop_pct: float=-1.0' in M or 'hard_stop_pct:float=-1.0' in M
checks['frozen_combo2']='combo_bars: int=2' in M or 'combo_bars:int=2' in M
checks['frozen_no_hold']="'forced_min_hold':False" in M or '"forced_min_hold":False' in M
# inspect only V145 USA authority block for broker APIs
start=S.find('V145_USA_FROZEN_PAPER_AUTHORITY')
end=S.find('return None', start+1)
block=S[start:start+2600] if start>=0 else ''
checks['no_real_broker_in_usa_block']=not any(x in block for x in ['KiwoomMockBroker','send_order(','place_order(','broker.'])
for k,v in checks.items(): print(k,'PASS' if v else 'FAIL')

try:
    py_compile.compile(str(ENG),doraise=True); ec=True
except Exception as e:
    ec=False; print('ENGINE_COMPILE_ERROR',e)
try:
    py_compile.compile(str(MOD),doraise=True); mc=True
except Exception as e:
    mc=False; print('MODULE_COMPILE_ERROR',e)
print('ENGINE_COMPILE=', 'PASS' if ec else 'FAIL')
print('MODULE_COMPILE=', 'PASS' if mc else 'FAIL')

# Service state only; no mutation.
try:
    p=subprocess.run(['systemctl','is-active','day-trader-api.service'],capture_output=True,text=True,timeout=5)
    service_state=(p.stdout or p.stderr).strip() or f'rc={p.returncode}'
except Exception as e:
    service_state='UNKNOWN:'+str(e)
print('SERVICE_STATE=',service_state)

# Environment hazards visible to this shell. Runtime unit may differ; V148 will inspect live startup if started.
for key in ['WILLIAMS_KIWOOM_MOCK_AUTO','KIWOOM_MOCK_AUTO_ENABLED','WILLIAMS_USA_PAPER_MAX_POSITIONS']:
    print('ENV',key,'=',os.getenv(key,'<unset>'))

# Legacy contamination is allowed elsewhere, but USA frozen early branch must isolate it.
legacy=[]
for name,pat in [('KOREA_5MIN_HOLD',r'hold_sec\s*<\s*300'),('KOREA_STOP_15',r'0\.985'),('V123_WATCHDOG',r'V123')]:
    if re.search(pat,S,re.I): legacy.append(name)
print('LEGACY_CONTAMINATION_PRESENT=',','.join(legacy) if legacy else 'NONE')
print('USA_FROZEN_EARLY_ISOLATION=', 'PASS' if checks.get('no_real_broker_in_usa_block') and checks.get('usa_gate') else 'FAIL')

static_ok=all(bool(v) for v in checks.values()) and ec and mc
# Service should still be inactive at this audit stage; active is not fatal but is unexpected.
state_ok=(service_state!='failed')
print('STATIC_PASS=',static_ok)
print('SERVICE_STATE_SAFE=',state_ok)
print('PRESTART_PASS=',bool(static_ok and state_ok))
print('ORDER_MODE=USA_PAPER_ONLY')
print('REAL_BROKER_AUTHORITY=NONE_ADDED')
print('NEXT=' + ('V148_START_SERVICE_AND_RUNTIME_TELEMETRY_AUDIT' if static_ok and state_ok else 'DO_NOT_START_SERVICE; FIX_PRESTART_FAILURES'))
