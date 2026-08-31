#!/usr/bin/env python3
from pathlib import Path
import os, py_compile, subprocess, tempfile, time, json, re, urllib.request

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
A=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

s=P.read_text(encoding='utf-8')
if 'V55_USD_RESULT_LIST_PARSE = True' not in s:
    marker=None
    for m in ('V50_ACCOUNT_RATE_LIMIT_SAFE = True','V49_LIVE_EXCHANGE_RESOLUTION = True','V48_TARGETED_BALANCE_FALLBACK = True'):
        if m in s:
            marker=m; break
    if not marker: raise SystemExit('ABORT V55 marker missing')
    s=s.replace(marker,marker+'\nV55_USD_RESULT_LIST_PARSE = True',1)

    # Patch only inside publish_live_account; preserve existing indentation exactly.
    start=s.find('def publish_live_account():')
    if start<0: raise SystemExit('ABORT publish_live_account missing')
    end=s.find('\n\ndef ',start+10)
    if end<0: end=len(s)
    block=s[start:end]

    # Replace existing cash/orderable calculation, regardless of V47/V48/V50 variant.
    patterns=[
        r"(?m)^\s*cash=f\(dep\.get\('fc_entra'\)\)\s*$",
        r"(?ms)^\s*cash_vals=_recursive_numbers\(dep,'fc_entra'\)\n\s*order_vals=_recursive_numbers\(dep,'fc_pymn_alowa'\)\n\s*cash=max\(cash_vals\) if cash_vals else 0\.0\n\s*orderable=max\(order_vals\) if order_vals else cash\s*$",
    ]
    replacement=(
        "    usd_row=next((x for x in (dep.get('result_list') or []) if str(x.get('crnc_code') or '').upper()=='USD'), {})\n"
        "    cash=f(usd_row.get('fc_entra')) if usd_row else 0.0\n"
        "    orderable_cash=f((usd_row.get('fc_ord_alowa') or usd_row.get('fc_pymn_alowa') or cash)) if usd_row else cash\n"
        "    orderable=orderable_cash"
    )
    replaced=False
    for pat in patterns:
        nb,n=re.subn(pat,replacement,block,count=1)
        if n:
            block=nb; replaced=True; break
    if not replaced:
        # Fallback: add calculation immediately after dep assignment, with exact function indentation.
        dep_pat=r"(?m)^(\s*)dep\s*=\s*b\.deposit_usd\(\)\s*$"
        m=re.search(dep_pat,block)
        if not m: raise SystemExit('ABORT deposit_usd assignment missing')
        indent=m.group(1)
        add=(
            m.group(0)+"\n"+
            indent+"usd_row=next((x for x in (dep.get('result_list') or []) if str(x.get('crnc_code') or '').upper()=='USD'), {})\n"+
            indent+"cash=f(usd_row.get('fc_entra')) if usd_row else 0.0\n"+
            indent+"orderable_cash=f((usd_row.get('fc_ord_alowa') or usd_row.get('fc_pymn_alowa') or cash)) if usd_row else cash\n"+
            indent+"orderable=orderable_cash"
        )
        block=block[:m.start()]+add+block[m.end():]

    # Make sure payload contains orderable_cash but do not alter surrounding indentation.
    if "'orderable_cash':" not in block and '"orderable_cash":' not in block:
        block=block.replace("'cash':cash,","'cash':cash,'orderable_cash':orderable_cash,",1)
        block=block.replace('"cash":cash,','"cash":cash,"orderable_cash":orderable_cash,',1)

    s=s[:start]+block+s[end:]

fd,name=tempfile.mkstemp(prefix='v55_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8')
try:
    py_compile.compile(str(t),doraise=True)
except Exception:
    # Never install a non-compiling runtime.
    print(t.read_text(encoding='utf-8').splitlines()[310:355],flush=True)
    raise
print('PY_COMPILE=PASS',flush=True)
bak=Path(str(P)+'.pre_v55')
if not bak.exists(): subprocess.run(['sudo','cp','-a',P,bak],check=True)
subprocess.run(['sudo','install','-m','0644',t,P],check=True); t.unlink(missing_ok=True)

old=A.stat().st_mtime if A.exists() else 0
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
end=time.time()+55; d={}
while time.time()<end:
    try:
        if A.exists() and A.stat().st_mtime>old:
            d=json.loads(A.read_text(encoding='utf-8'))
            if float(d.get('cash') or 0)>0: break
    except Exception: pass
    time.sleep(2)

print('US_MOCK_ACCOUNT='+json.dumps({
    'total_assets':d.get('total_assets'),'cash':d.get('cash'),
    'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),
    'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []],
    'errors':d.get('errors')
},ensure_ascii=False),flush=True)
if abs(float(d.get('cash') or 0)-99509.317)>1.0:
    raise SystemExit('ABORT USD cash did not match verified ust21110 account')
if abs(float(d.get('orderable_cash') or 0)-94761.660)>1.0:
    raise SystemExit('ABORT USD orderable cash did not match verified ust21110 account')

st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
fr=(st.get('finder') or {}).get('rows') or []
print('USA_SESSION='+str(st.get('session')),flush=True)
print('USA_FINDER_ROWS='+str(len(fr)),flush=True)
print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'),flush=True)
print('US_CASH_SOURCE=UST21110_RESULT_LIST_USD',flush=True)
print('US_ORDERABLE_CASH_SOURCE=FC_ORD_ALOWA_OR_FC_PYMN_ALOWA',flush=True)
print('DEPLOY=PASS',flush=True)
