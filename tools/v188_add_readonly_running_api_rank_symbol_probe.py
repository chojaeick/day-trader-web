from pathlib import Path
import subprocess, sys, textwrap

API=Path('/home/ubuntu/day-trader-api/live_server/api.py')
VENV='/home/ubuntu/day-trader-api/venv/bin/python3'
print('=== V188 ADD READONLY RUNNING-API RANK SYMBOL PROBE ===')
print('STRATEGY_CHANGE=NONE ORDER_CHANGE=NONE KOREA_PATH_CHANGE=NONE')
if not API.exists():
    print('API_NOT_FOUND'); raise SystemExit(2)
text=API.read_text()
marker='# V188_READONLY_KIWOOM_RANK_SYMBOL_PROBE'
if marker in text:
    print('ALREADY_PATCHED=True')
else:
    insert='''\n\n# V188_READONLY_KIWOOM_RANK_SYMBOL_PROBE\n@app.get('/api/v4/USA/debug/rank-symbol/{symbol}')\ndef v188_rank_symbol_probe(symbol:str):\n    symbol=str(symbol or '').upper().strip()\n    out={'ok':True,'symbol':symbol,'hits':[],'quote_matrix':{}}\n    methods=[('usa20530', lambda: k.ranking_today_volume('0')),\n             ('usa20910_up', lambda: k.ranking_change_rate('1')),\n             ('usa20910_dn', lambda: k.ranking_change_rate('4')),\n             ('usa20520', lambda: k.ranking_volume_surge())]\n    for name,fn in methods:\n        try:\n            rows=fn() or []\n            for r in rows:\n                if str(r.get('stk_cd') or '').upper().strip()==symbol:\n                    out['hits'].append({'source':name,'row':r})\n        except Exception as e:\n            out.setdefault('errors',[]).append({'source':name,'error':repr(e)})\n    for ex in ('ND','NY','NA'):\n        try:\n            out['quote_matrix'][ex]=k.quote(symbol,ex)\n        except Exception as e:\n            out['quote_matrix'][ex]={'error':repr(e)}\n    return out\n'''
    anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)"
    if anchor not in text:
        print('ANCHOR_NOT_FOUND'); raise SystemExit(3)
    text=text.replace(anchor,anchor+insert,1)
    API.with_suffix('.py.bak_v188').write_text(API.read_text())
    API.write_text(text)
    print('PATCHED',API)
for f in [API,Path('/home/ubuntu/day-trader-api/live_server/kiwoom.py')]:
    r=subprocess.run([VENV,'-m','py_compile',str(f)],capture_output=True,text=True)
    print('PY_COMPILE',f.name,'PASS' if r.returncode==0 else 'FAIL')
    if r.returncode: print(r.stderr); raise SystemExit(4)
r=subprocess.run(['sudo','systemctl','restart','day-trader-api.service'])
print('RESTART_RC=',r.returncode)
print('NEXT=CALL_DEBUG_ENDPOINT_FOR_STALE11_AND_COMPARE_STK_CD_STEX_TP')
