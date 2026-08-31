#!/usr/bin/env python3
from pathlib import Path
import os, py_compile, subprocess, tempfile, time, json, urllib.request

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
A=R/'v22e_us_mock_account.json'

s=P.read_text(encoding='utf-8')
if 'V53_USD_RESULT_LIST_PARSE = True' not in s:
    marker='V50_ACCOUNT_RATE_LIMIT_SAFE = True'
    if marker not in s: marker='V49_LIVE_EXCHANGE_RESOLUTION = True'
    if marker not in s: raise SystemExit('ABORT marker missing')
    s=s.replace(marker,marker+'\nV53_USD_RESULT_LIST_PARSE = True',1)

    old="    cash=f(dep.get('fc_entra'))\n"
    new="    usd_row=next((x for x in (dep.get('result_list') or []) if str(x.get('crnc_code') or '').upper()=='USD'), {})\n    cash=f(usd_row.get('fc_entra'))\n    orderable_cash=f(usd_row.get('fc_ord_alowa') or usd_row.get('fc_pymn_alowa') or cash)\n"
    if old in s:
        s=s.replace(old,new,1)
    else:
        old2="    cash_vals=_recursive_numbers(dep,'fc_entra')\n    order_vals=_recursive_numbers(dep,'fc_pymn_alowa')\n    cash=max(cash_vals) if cash_vals else 0.0\n    orderable=max(order_vals) if order_vals else cash\n"
        if old2 not in s: raise SystemExit('ABORT cash parser anchor missing')
        s=s.replace(old2,new,1)

    s=s.replace("'currency':str(bal.get('crnc_code') or 'USD'),'cash':cash,","'currency':'USD','cash':cash,'orderable_cash':orderable_cash,")
    s=s.replace("'cash':cash,'orderable_cash':orderable,","'cash':cash,'orderable_cash':orderable_cash,")

fd,name=tempfile.mkstemp(prefix='v53_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8'); py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS')
subprocess.run(['sudo','cp','-a',P,str(P)+'.pre_v53'],check=False)
subprocess.run(['sudo','install','-m','0644',t,P],check=True); t.unlink(missing_ok=True)
old=A.stat().st_mtime if A.exists() else 0
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us'],check=True)
end=time.time()+45; d={}
while time.time()<end:
    try:
        if A.exists() and A.stat().st_mtime>old:
            d=json.loads(A.read_text(encoding='utf-8'))
            if float(d.get('cash') or 0)>0: break
    except Exception: pass
    time.sleep(2)
print('US_MOCK_ACCOUNT='+json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []]},ensure_ascii=False))
if float(d.get('cash') or 0)<=0: raise SystemExit('ABORT USD cash still zero')
st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
fr=(st.get('finder') or {}).get('rows') or []
print('USA_SESSION='+str(st.get('session')))
print('USA_FINDER_ROWS='+str(len(fr)))
print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'))
print('US_CASH_SOURCE=UST21110_RESULT_LIST_USD')
print('DEPLOY=PASS')
