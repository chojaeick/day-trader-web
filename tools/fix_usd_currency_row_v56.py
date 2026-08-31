#!/usr/bin/env python3
from pathlib import Path
import os, py_compile, subprocess, tempfile, time, json, urllib.request

R=Path('/home/ubuntu/day-trader-api')
P=R/'live_server'/'v22e_us_mock_live.py'
A=R/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'

s=P.read_text(encoding='utf-8')
if 'V56_USD_ONLY_CURRENCY_ROW = True' not in s:
    marker=None
    for m in ('V50_ACCOUNT_RATE_LIMIT_SAFE = True','V49_LIVE_EXCHANGE_RESOLUTION = True','V48_TARGETED_BALANCE_FALLBACK = True'):
        if m in s:
            marker=m; break
    if not marker:
        raise SystemExit('ABORT V56 marker missing')
    s=s.replace(marker,marker+'\nV56_USD_ONLY_CURRENCY_ROW = True',1)

    start=s.find('def publish_live_account():')
    if start<0:
        raise SystemExit('ABORT publish_live_account missing')
    end=s.find('\n\ndef ',start+10)
    if end<0: end=len(s)
    block=s[start:end]

    dep_pos=block.find('dep=b.deposit_usd()')
    if dep_pos<0:
        dep_pos=block.find('dep = b.deposit_usd()')
    if dep_pos<0:
        raise SystemExit('ABORT deposit_usd call missing')

    payload_pos=block.find('payload={')
    if payload_pos<0:
        payload_pos=block.find('payload = {')
    if payload_pos<0:
        raise SystemExit('ABORT payload missing')

    indent='    '
    inject=(
        indent+"# V56: ust21110 contains multiple currencies; use the USD row only.\n"
        +indent+"usd_row=next((x for x in (dep.get('result_list') or []) if str(x.get('crnc_code') or '').upper()=='USD'), {})\n"
        +indent+"if usd_row:\n"
        +indent*2+"cash=f(usd_row.get('fc_entra'))\n"
        +indent*2+"orderable_cash=f(usd_row.get('fc_ord_alowa') or usd_row.get('fc_pymn_alowa') or cash)\n"
        +indent*2+"orderable=orderable_cash\n"
    )

    block=block[:payload_pos]+inject+block[payload_pos:]
    if "'orderable_cash':" not in block:
        block=block.replace("'cash':cash,","'cash':cash,'orderable_cash':orderable_cash,",1)
    s=s[:start]+block+s[end:]

fd,name=tempfile.mkstemp(prefix='v56_',suffix='.py'); os.close(fd)
t=Path(name); t.write_text(s,encoding='utf-8')
py_compile.compile(str(t),doraise=True)
print('PY_COMPILE=PASS',flush=True)

bak=Path(str(P)+'.pre_v56')
if not bak.exists():
    subprocess.run(['sudo','cp','-a',P,bak],check=True)
subprocess.run(['sudo','install','-m','0644',t,P],check=True)
t.unlink(missing_ok=True)

old=A.stat().st_mtime if A.exists() else 0
subprocess.run(['sudo','systemctl','restart',SERVICE],check=True)
end=time.time()+50; d={}
while time.time()<end:
    try:
        if A.exists() and A.stat().st_mtime>old:
            d=json.loads(A.read_text(encoding='utf-8'))
            if float(d.get('cash') or 0)>0: break
    except Exception:
        pass
    time.sleep(2)

print('US_MOCK_ACCOUNT='+json.dumps({
    'total_assets':d.get('total_assets'),'cash':d.get('cash'),
    'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),
    'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []],
    'errors':d.get('errors')
},ensure_ascii=False),flush=True)

cash=float(d.get('cash') or 0)
orderable=float(d.get('orderable_cash') or 0)
stock=float(d.get('stock_value') or 0)
if not (99000 <= cash <= 100500):
    raise SystemExit(f'ABORT USD cash out of verified range: {cash}')
if not (94000 <= orderable <= 95500):
    raise SystemExit(f'ABORT USD orderable cash out of verified range: {orderable}')
if stock < 0:
    raise SystemExit('ABORT invalid stock value')

st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
fr=(st.get('finder') or {}).get('rows') or []
print('USA_SESSION='+str(st.get('session')),flush=True)
print('USA_FINDER_ROWS='+str(len(fr)),flush=True)
print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'),flush=True)
print('US_CASH_SOURCE=UST21110_RESULT_LIST_USD_ONLY',flush=True)
print('US_HOLDINGS_SOURCE=KIWOOM_US_MOCK',flush=True)
print('DEPLOY=PASS',flush=True)
