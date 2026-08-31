#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, sys

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
SRC=ROOT/'app_v5.py.pre_v76_1788195415'
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
PY=ROOT/'venv/bin/python'
FLAG=ROOT/'v22e_us_trade_enabled.flag'

print('V82_START=YES')
if not SRC.exists(): print('ABORT=PRE_V76_MISSING'); sys.exit(2)
r=subprocess.run([str(PY),'-m','py_compile',str(SRC)],capture_output=True,text=True)
if r.returncode: print('ABORT=PRE_V76_NOT_COMPILE_PASS'); print(r.stderr); sys.exit(3)

bak=ROOT/f'app_v5.py.pre_v82_{int(time.time())}'
if APP.exists(): shutil.copy2(APP,bak)
shutil.copy2(SRC,APP)
lines=APP.read_text(encoding='utf-8').splitlines()

# helpers before render_positions exact function line
idx=next((i for i,l in enumerate(lines) if l.startswith('def render_positions(market,tracker):')),None)
if idx is None: print('ABORT=RENDER_POSITIONS_NOT_FOUND'); shutil.copy2(bak,APP); sys.exit(4)
helper=[
"# V82_US_RUNTIME_BRIDGE",
"def _v82_json(path, default):",
"    import json",
"    try: return json.loads(Path(path).read_text(encoding='utf-8'))",
"    except Exception: return default",
"",
"def v82_us_account():",
"    d=_v82_json('/home/ubuntu/day-trader-api/v22e_us_mock_account.json',{})",
"    return d if isinstance(d,dict) else {}",
"",
"def v82_us_eval_rows():",
"    d=_v82_json('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})",
"    if isinstance(d,list): return d",
"    if isinstance(d,dict) and isinstance(d.get('rows'),list): return d.get('rows')",
"    return []",
"",
"def v82_us_trade_enabled():",
"    p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')",
"    if not p.exists(): return True",
"    try: return p.read_text(encoding='utf-8').strip()!='0'",
"    except Exception: return True",
"",
"def v82_set_us_trade_enabled(v):",
"    Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag').write_text('1' if v else '0',encoding='utf-8')",
""
]
lines[idx:idx]=helper

# re-find render_positions after helper insertion and inject exact USA branch after def
idx=next(i for i,l in enumerate(lines) if l.startswith('def render_positions(market,tracker):'))
us_branch=[
"    if market=='USA':",
"        acct=v82_us_account(); hs=acct.get('holdings') if isinstance(acct,dict) else []",
"        hs=hs if isinstance(hs,list) else []",
"        st.markdown('<div class=\"v5-section-title\">🛡 보유주식 관리</div><div class=\"v5-section-sub\">Kiwoom US 모의계좌 실시간 보유종목</div>',unsafe_allow_html=True)",
"        if not hs:",
"            st.info('현재 Kiwoom US 모의계좌 보유종목이 없습니다.')",
"            return",
"        import pandas as pd",
"        out=[]",
"        for h in hs:",
"            if not isinstance(h,dict): continue",
"            out.append({'종목':h.get('symbol') or h.get('stk_cd') or '-', '수량':h.get('qty') or h.get('hold_qty') or 0, '평균가':h.get('avg') or h.get('frgn_stk_book_uv') or 0, '현재가':h.get('price') or h.get('now_pric') or 0, '평가액':h.get('market_value') or h.get('evlt_amt') or 0, '손익':h.get('pnl') or h.get('pl_amt') or 0, '손익률%':h.get('pnl_pct') or h.get('pl_rt') or 0})",
"        st.dataframe(pd.DataFrame(out),use_container_width=True,hide_index=True)",
"        return"
]
lines[idx+1:idx+1]=us_branch

# exact status anchor
sidx=next((i for i,l in enumerate(lines) if l.strip()=="status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)"),None)
if sidx is None: print('ABORT=STATUS_LINE_NOT_FOUND'); shutil.copy2(bak,APP); sys.exit(5)
status_block=[
"if market=='USA':",
"    _v82_rows=v82_us_eval_rows()",
"    if _v82_rows:",
"        finders=_v82_rows[:20]; trackers=_v82_rows",
"        _v82_eval=_v82_json('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})",
"        if isinstance(_v82_eval,dict) and _v82_eval.get('session'):",
"            status=dict(status or {}); status['session']=_v82_eval.get('session')"
]
lines[sidx+1:sidx+1]=status_block

# exact US account line and trade switch immediately before it
uidx=next((i for i,l in enumerate(lines) if l.strip().startswith("_ua=v45_us_live_account(); total=f(_ua.get('total_assets'))")),None)
if uidx is None: print('ABORT=US_ACCOUNT_LINE_NOT_FOUND'); shutil.copy2(bak,APP); sys.exit(6)
indent=lines[uidx][:len(lines[uidx])-len(lines[uidx].lstrip())]
switch=[
indent+"_v82_trade=v82_us_trade_enabled()",
indent+"_v82_new=st.toggle('US 실제 자동매매',value=_v82_trade,key='v82_us_trade_toggle')",
indent+"if _v82_new!=_v82_trade:",
indent+"    v82_set_us_trade_enabled(_v82_new); st.rerun()"
]
lines[uidx:uidx]=switch

APP.write_text('\n'.join(lines)+'\n',encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
if r.returncode:
    print('PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,APP); print('APP_ROLLBACK=YES'); sys.exit(7)
print('PY_COMPILE=PASS')

# engine guard only
eng=ENGINE.read_text(encoding='utf-8')
if 'V82_US_TRADE_SWITCH_GUARD' not in eng:
    target='def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n'
    if target not in eng: print('ABORT=ENGINE_ORDER_ONCE_NOT_FOUND'); shutil.copy2(bak,APP); sys.exit(8)
    guard="""# V82_US_TRADE_SWITCH_GUARD\ndef _v82_trade_enabled():\n    try:\n        p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n        return (not p.exists()) or p.read_text(encoding='utf-8').strip()!='0'\n    except Exception:\n        return True\n\n"""
    eng=eng.replace(target,guard+target,1)
    start=eng.index(target)+len(target)
    eng=eng[:start]+"    if not _v82_trade_enabled():\n        log('ORDER_BLOCKED_TRADE_SWITCH_OFF',side=side,symbol=sym,qty=qty,reason=reason)\n        return {'ok':False,'reason':'TRADE_SWITCH_OFF'}\n"+eng[start:]
    ebak=ENGINE.with_suffix('.py.pre_v82')
    shutil.copy2(ENGINE,ebak)
    ENGINE.write_text(eng,encoding='utf-8')
    er=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
    if er.returncode:
        print('ENGINE_PY_COMPILE=FAIL'); print((er.stderr or er.stdout).strip()); shutil.copy2(ebak,ENGINE); shutil.copy2(bak,APP); print('ROLLBACK=YES'); sys.exit(9)
print('ENGINE_PY_COMPILE=PASS')
if not FLAG.exists(): FLAG.write_text('1',encoding='utf-8')
subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
subprocess.run("pkill -f 'streamlit run .*app_v5.py' || true",shell=True)
time.sleep(1)
subprocess.run("cd /home/ubuntu/day-trader-api && nohup /home/ubuntu/day-trader-api/venv/bin/streamlit run app_v5.py --server.port 8503 --server.address 0.0.0.0 > /home/ubuntu/day-trader-api/app_v5.log 2>&1 &",shell=True)
time.sleep(4)
svc=subprocess.run(['systemctl','is-active','day-trader-v22e-us.service'],capture_output=True,text=True).stdout.strip()
print('V22E_SERVICE='+svc.upper())
try:
    import urllib.request
    urllib.request.urlopen('http://127.0.0.1:8503/',timeout=5).read(64)
    print('V5_HTTP=PASS')
except Exception as e:
    print('V5_HTTP=FAIL',repr(e)); sys.exit(10)
print('APP_BASE=PRE_V76_COMPILE_PASS')
print('US_POSITIONS_SOURCE=KIWOOM_ACCOUNT_FILE')
print('FINDER_SOURCE=V22E_LIVE_EVAL_DIRECT')
print('TRADE_SWITCH=V5_TO_ENGINE_ORDER_GUARD')
print('KR_PATH=UNCHANGED')
print('DEPLOY=PASS')
