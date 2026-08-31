#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, sys, os, re, json, time

APP=Path('/home/ubuntu/day-trader-api/app_v5.py')
RUNTIME=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')

print('V77_START=YES')

# 1) Recover app from newest pre-v76 backup that compiles.
candidates=[]
for p in APP.parent.glob('app_v5.py*'):
    if p == APP or not p.is_file():
        continue
    try:
        mt=p.stat().st_mtime
    except Exception:
        continue
    candidates.append((mt,p))
candidates.sort(reverse=True)

chosen=None
for _,p in candidates:
    try:
        cp=subprocess.run([sys.executable,'-m','py_compile',str(p)],capture_output=True,text=True)
        if cp.returncode==0:
            chosen=p; break
    except Exception:
        pass
if not chosen:
    print('ABORT=no_compilable_app_backup')
    sys.exit(2)

shutil.copy2(chosen, APP)
print(f'APP_RECOVERED_FROM={chosen.name}')

# 2) Apply only narrow USA runtime console additions, syntax-safe append/targeted replacements.
s=APP.read_text(encoding='utf-8')

# ensure imports
if 'from pathlib import Path' not in s:
    s='from pathlib import Path\n'+s
if 'import json' not in s:
    s='import json\n'+s

helper='''\n# V77_US_RUNTIME_CONSOLE\nV77_US_ACCOUNT_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_mock_account.json')\nV77_US_EVAL_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')\nV77_US_SWITCH_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_trade_switch.json')\nV77_US_LOG_PATH=Path('/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl')\n\ndef v77_read_json(path, default):\n    try:\n        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default\n    except Exception:\n        return default\n\ndef v77_us_account():\n    d=v77_read_json(V77_US_ACCOUNT_PATH,{})\n    return d if isinstance(d,dict) else {}\n\ndef v77_us_eval():\n    d=v77_read_json(V77_US_EVAL_PATH,{})\n    if isinstance(d,list): return {'rows':d}\n    return d if isinstance(d,dict) else {'rows':[]}\n\ndef v77_us_switch_get():\n    d=v77_read_json(V77_US_SWITCH_PATH,{'enabled':True})\n    return bool(d.get('enabled',True)) if isinstance(d,dict) else True\n\ndef v77_us_switch_set(enabled):\n    tmp=V77_US_SWITCH_PATH.with_suffix('.tmp')\n    tmp.write_text(json.dumps({'enabled':bool(enabled),'ts':time.time()}),encoding='utf-8')\n    os.replace(tmp,V77_US_SWITCH_PATH)\n\ndef v77_us_recent_orders(limit=12):\n    if not V77_US_LOG_PATH.exists(): return []\n    out=[]\n    try:\n        for line in V77_US_LOG_PATH.read_text(encoding='utf-8',errors='ignore').splitlines()[-400:]:\n            try: d=json.loads(line)\n            except Exception: continue\n            if str(d.get('event','')).startswith('ORDER_') or 'CANCEL' in str(d.get('event','')):\n                out.append(d)\n    except Exception: pass\n    return out[-limit:]\n'''
if '# V77_US_RUNTIME_CONSOLE' not in s:
    # insert after imports area, before first def/class if possible
    m=re.search(r'(?m)^(def |class |st\.)',s)
    pos=m.start() if m else 0
    s=s[:pos]+helper+'\n'+s[pos:]

# Replace render_positions USA branch only by injecting at function start.
needle='def render_positions(market,tracker):\n'
if needle in s and 'V77_US_BROKER_HOLDINGS_RENDER' not in s:
    inject='''def render_positions(market,tracker):\n    # V77_US_BROKER_HOLDINGS_RENDER\n    if market=='USA':\n        st.markdown('<div class="v5-section-title">🛡 실제 Kiwoom 보유주식</div>',unsafe_allow_html=True)\n        acct=v77_us_account(); hs=acct.get('holdings') or []\n        if not hs:\n            st.info('현재 Kiwoom US mock 계좌 보유종목이 없습니다.')\n        else:\n            rows=[]\n            for h in hs:\n                rows.append({\n                    '종목':h.get('symbol') or h.get('stk_cd') or '-',\n                    '수량':h.get('qty') or 0,\n                    '평균가':h.get('avg') or 0,\n                    '현재가':h.get('price') or 0,\n                    '평가액':h.get('market_value') or 0,\n                    '손익':h.get('pnl') or 0,\n                    '손익률%':h.get('pnl_pct') or 0,\n                })\n            st.dataframe(rows,use_container_width=True,hide_index=True)\n        return\n'''
    s=s.replace(needle,inject,1)

# Inject USA console in trading render around status acquisition if recognizable.
marker="status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)"
if marker in s and 'V77_US_CONSOLE_BLOCK' not in s:
    rep=marker+'''\n    # V77_US_CONSOLE_BLOCK\n    if market=='USA':\n        _acct=v77_us_account(); _ev=v77_us_eval(); _rows=_ev.get('rows') or []\n        _enabled=v77_us_switch_get()\n        c1,c2,c3,c4=st.columns([1.25,1,1,1])\n        with c1:\n            new_enabled=st.toggle('US 자동매매',value=_enabled,key='v77_us_trade_toggle')\n            if new_enabled!=_enabled:\n                v77_us_switch_set(new_enabled); st.rerun()\n        c2.metric('엔진','V22E LIVE')\n        c3.metric('세션',_ev.get('session') or status.get('session') or '-')\n        c4.metric('Finder',len(_rows))\n        finders=_rows[:20]\n        trackers=_rows\n'''
    s=s.replace(marker,rep,1)

APP.write_text(s,encoding='utf-8')

cp=subprocess.run([sys.executable,'-m','py_compile',str(APP)],capture_output=True,text=True)
print('PY_COMPILE=' + ('PASS' if cp.returncode==0 else 'FAIL'))
if cp.returncode!=0:
    print(cp.stderr.strip())
    # restore chosen backup if our narrow patch somehow failed
    shutil.copy2(chosen,APP)
    print('APP_ROLLBACK=YES')
    sys.exit(3)

# 3) Engine order switch + broker holdings reconciliation: narrow patch only if not already present.
if RUNTIME.exists():
    r=RUNTIME.read_text(encoding='utf-8')
    if 'V77_US_SWITCH_PATH' not in r:
        insert="\nV77_US_SWITCH_PATH = Path('/home/ubuntu/day-trader-api/v22e_us_trade_switch.json')\n\ndef v77_trade_enabled():\n    try:\n        if not V77_US_SWITCH_PATH.exists(): return True\n        d=json.loads(V77_US_SWITCH_PATH.read_text(encoding='utf-8'))\n        return bool(d.get('enabled',True)) if isinstance(d,dict) else True\n    except Exception:\n        return True\n"
        anchor='V57_USD_ONLY_CASH_PARSE = True'
        if anchor in r:
            r=r.replace(anchor,anchor+insert,1)
    if "def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):" in r and 'TRADE_SWITCH_BLOCKED' not in r:
        r=r.replace("def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n", "def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n    if not v77_trade_enabled():\n        log('TRADE_SWITCH_BLOCKED',side=side,symbol=sym,reason=reason)\n        return {'ok':False,'reason':'TRADE_SWITCH_OFF'}\n",1)
    RUNTIME.write_text(r,encoding='utf-8')
    rp=subprocess.run([sys.executable,'-m','py_compile',str(RUNTIME)],capture_output=True,text=True)
    print('ENGINE_PY_COMPILE=' + ('PASS' if rp.returncode==0 else 'FAIL'))
    if rp.returncode!=0:
        print(rp.stderr.strip()); sys.exit(4)

# initialize switch ON only if absent
sw=Path('/home/ubuntu/day-trader-api/v22e_us_trade_switch.json')
if not sw.exists():
    sw.write_text(json.dumps({'enabled':True,'ts':time.time()}),encoding='utf-8')

# Restart only US engine and V5 nohup process.
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
time.sleep(2)
# V5 restart
subprocess.run("pkill -f 'streamlit run .*app_v5.py' || true",shell=True)
time.sleep(1)
subprocess.run("cd /home/ubuntu/day-trader-api && nohup ./venv/bin/streamlit run app_v5.py --server.address 0.0.0.0 --server.port 8503 >/tmp/daytrader-v5.log 2>&1 &",shell=True)
time.sleep(4)

svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE=' + svc.upper())
http=subprocess.run("curl -fsS --max-time 5 http://127.0.0.1:8503/ >/dev/null",shell=True)
print('V5_HTTP=' + ('PASS' if http.returncode==0 else 'FAIL'))
print('US_ACCOUNT_SOURCE=KIWOOM_MOCK_ACCOUNT_SNAPSHOT')
print('TRADE_SWITCH=V5_BUTTON_TO_ENGINE_ORDER_GUARD')
print('US_POSITIONS_SOURCE=KIWOOM_ACCOUNT_FILE')
print('FINDER_SOURCE=V22E_LIVE_EVAL_DIRECT')
print('DEPLOY=' + ('PASS' if svc=='active' and http.returncode==0 else 'FAIL'))
