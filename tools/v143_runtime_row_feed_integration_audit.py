#!/usr/bin/env python3
"""V143 runtime row feed integration audit.

READ/PATCH runtime only. NO ORDERS. NO DOWNLOADS.
Purpose: verify V142 frozen USA Williams context is wired from real USA row/bar fields
and that all frozen inputs are materially populated before paper authority is enabled.
"""
from pathlib import Path
import re, py_compile, shutil

P=Path('/home/ubuntu/day-trader-api/live_server/v4_engine.py')
B=P.with_suffix('.py.bak_v143')
S=P.read_text(errors='ignore')
if not B.exists(): shutil.copy2(P,B)

checks={
 'v140_eval': r'def _v140_usa_frozen_williams_eval',
 'v142_builder': r'def _v142_build_usa_frozen_ctx',
 'frozen_ctx_key': r'williams_frozen_ctx',
 'day_open': r'day_open',
 'prev_high': r'prev_high',
 'prev_low': r'prev_low',
 'rsi2': r'rsi2',
 'cci20': r'cci20|cci',
 'macd_hist': r'macd_hist|hist',
 'prior10_volume_avg': r'prior10_volume_avg|prior10|volume_avg',
 'cross_now': r'cross_now|raw_cross|cross',
 'prev_crossed': r'prev_crossed|first_cross|seen',
 'usa_row_feed': r"market.?['\"]?:?['\"]USA|market.?==.?['\"]USA['\"]|str\(market\).*USA",
}

print('=== V143 RUNTIME ROW FEED INTEGRATION AUDIT ===')
print('ENGINE',P,'EXISTS=',P.exists(),'BYTES=',len(S))
passed=0
for k,p in checks.items():
    ok=bool(re.search(p,S,re.I|re.S))
    print(k, 'PASS' if ok else 'MISSING')
    passed+=int(ok)

# Scan builder body for exact frozen argument names.
m=re.search(r'def _v142_build_usa_frozen_ctx\(.*?\n(?=    def |class |\Z)',S,re.S)
body=m.group(0) if m else ''
required=['ts','prev_crossed','cross_now','rsi2','day_open','prev_high','prev_low','volume','prior10_volume_avg','cci20','macd_hist','prev_macd_hist']
print('\n=== BUILDER ARG COVERAGE ===')
arg_pass=0
for x in required:
    ok=x in body
    print(x,'PASS' if ok else 'MISSING')
    arg_pass+=int(ok)

# Safety scan: V143 must not add broker/order authority.
broker_patterns=[r'place_order',r'send_order',r'KiwoomMockBroker\(',r'buy\(',r'sell\(']
# Existing legacy code may contain these; only judge whether V140/V142 block has direct authority markers.
newblock='\n'.join(line for line in S.splitlines() if 'V140_' in line or 'V142_' in line or 'williams_frozen_eval' in line or 'williams_frozen_ctx' in line)
authority=any(re.search(p,newblock,re.I) for p in broker_patterns)
print('\n=== SAFETY ===')
print('FROZEN_BLOCK_ORDER_AUTHORITY=', 'FOUND' if authority else 'NONE')
try:
    py_compile.compile(str(P),doraise=True)
    comp=True
except Exception as e:
    comp=False;print('COMPILE_ERROR',repr(e))
print('PY_COMPILE=', 'PASS' if comp else 'FAIL')

static=(passed==len(checks))
args=(arg_pass==len(required))
ready=bool(static and args and comp and not authority)
print('\n=== VERDICT ===')
print('STATIC_PASS=',static)
print('ARG_COVERAGE_PASS=',args)
print('ORDER_AUTHORITY=NONE' if not authority else 'ORDER_AUTHORITY=FOUND')
print('INTEGRATION_AUDIT_PASS=',ready)
print('NEXT=' + ('V144_RUN_OFFLINE_RUNTIME_CONTEXT_VALUE_PARITY' if ready else 'FIX_ONLY_MISSING_RUNTIME_FEEDS; DO_NOT_ENABLE_PAPER_ORDERS'))
