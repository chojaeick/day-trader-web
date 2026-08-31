#!/usr/bin/env python3
from pathlib import Path
import json, os, py_compile, re, subprocess, tempfile, time, urllib.request

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
B=Path(str(P)+'.pre_v55')
A=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

if not B.exists():
    raise SystemExit('ABORT pre_v55 backup missing')
s=B.read_text(encoding='utf-8')

# Preserve all V50 and earlier working logic; add one clean marker.
marker=None
for m in ('V50_ACCOUNT_RATE_LIMIT_SAFE = True','V49_LIVE_EXCHANGE_RESOLUTION = True','V48_TARGETED_BALANCE_FALLBACK = True'):
    if m in s:
        marker=m; break
if not marker: raise SystemExit('ABORT baseline marker missing')
s=s.replace(marker,marker+'\nV57_USD_ONLY_CASH_PARSE = True',1)

start=s.find('def publish_live_account():')
if start<0: raise SystemExit('ABORT publish_live_account missing')
end=s.find('\n\ndef ',start+10)
if end<0: end=len(s)
block=s[start:end]

# Replace any pre-V55 cash parsing section with a deterministic USD-only parser.
# Insert immediately before payload construction, after deposit response is available.
payload_pos=block.find('    payload={')
if payload_pos<0: payload_pos=block.find('    payload = {')
if payload_pos<0: raise SystemExit('ABORT payload missing')

# Remove known old cash/orderable parser assignments so CNY max cannot survive.
lines=block.splitlines()
filtered=[]
skip_prefixes=(
    'cash_vals=', 'order_vals=', 'cash=max(', 'orderable=max(',
    "cash=f(dep.get('fc_entra'))", "orderable=f(dep.get('fc_pymn_alowa')",
)
for line in lines:
    stripped=line.strip()
    if any(stripped.startswith(p) for p in skip_prefixes):
        continue
    filtered.append(line)
block='\n'.join(filtered)

payload_pos=block.find('    payload={')
if payload_pos<0: payload_pos=block.find('    payload = {')
if payload_pos<0: raise SystemExit('ABORT payload missing after cleanup')

inject=(
"    usd_row=next((x for x in (dep.get('result_list') or []) if str(x.get('crnc_code') or '').upper()=='USD'), {})\n"
"    cash=f(usd_row.get('fc_entra')) if usd_row else 0.0\n"
"    orderable_cash=f(usd_row.get('fc_ord_alowa') or usd_row.get('fc_pymn_alowa') or cash) if usd_row else cash\n"
"    orderable=orderable_cash\n"
)
block=block[:payload_pos]+inject+block[payload_pos:]

# Ensure payload exposes orderable cash.
if "'orderable_cash':" not in block and '"orderable_cash":' not in block:
    if "'cash':cash," in block:
        block=block.replace("'cash':cash,","'cash':cash,'orderable_cash':orderable_cash,",1)
    elif '"cash":cash,' in block:
        block=block.replace('"cash":cash,','"cash":cash,"orderable_cash":orderable_cash,',1)
    else:
        raise SystemExit('ABORT cash payload field missing')

s=s[:start]+block+s[end:]

fd,name=tempfile.mkstemp(prefix='v57_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8')
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)

# Backup current V55 runtime before replacing it.
curbak=Path(str(P)+'.pre_v57_current')
if not curbak.exists(): subprocess.run(['sudo','cp','-a',P,curbak],check=True)
subprocess.run(['sudo','install','-m','0644',t,P],check=True)
t.unlink(missing_ok=True)

old=A.stat().st_mtime if A.exists() else 0
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
endtime=time.time()+60; d={}
while time.time()<endtime:
    try:
        if A.exists() and A.stat().st_mtime>old:
            d=json.loads(A.read_text(encoding='utf-8'))
            if float(d.get('cash') or 0)>0: break
    except Exception: pass
    time.sleep(2)

summary={
    'total_assets':d.get('total_assets'),'cash':d.get('cash'),
    'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),
    'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []],
    'errors':d.get('errors')
}
print('US_MOCK_ACCOUNT='+json.dumps(summary,ensure_ascii=False),flush=True)

cash=float(d.get('cash') or 0)
orderable=float(d.get('orderable_cash') or 0)
if not (90000 <= cash <= 110000): raise SystemExit('ABORT USD cash outside expected range')
if not (85000 <= orderable <= 105000): raise SystemExit('ABORT USD orderable cash outside expected range')
if cash>=200000: raise SystemExit('ABORT non-USD cash contamination detected')

st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
fr=(st.get('finder') or {}).get('rows') or []
print('USA_SESSION='+str(st.get('session')),flush=True)
print('USA_FINDER_ROWS='+str(len(fr)),flush=True)
print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'),flush=True)
print('US_CASH_SOURCE=UST21110_RESULT_LIST_USD_ONLY',flush=True)
print('US_HOLDINGS_SOURCE=KIWOOM_US_MOCK',flush=True)
print('DEPLOY=PASS',flush=True)
