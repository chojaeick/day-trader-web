#!/usr/bin/env python3
from pathlib import Path
import ast, shutil, subprocess, time, sys, re

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
SRC=ROOT/'app_v5.py.pre_v76_1788195415'
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
FLAG=ROOT/'v22e_us_trade_enabled.flag'
PY=ROOT/'venv/bin/python'

print('V80_START=YES')
if not SRC.exists():
    print('ABORT=PRE_V76_BACKUP_MISSING'); sys.exit(2)
r=subprocess.run([str(PY),'-m','py_compile',str(SRC)],capture_output=True,text=True)
if r.returncode:
    print('ABORT=PRE_V76_NOT_COMPILE_PASS'); print((r.stderr or r.stdout).strip()); sys.exit(3)

app_bak=ROOT/f'app_v5.py.pre_v80_{int(time.time())}'
if APP.exists(): shutil.copy2(APP,app_bak)
shutil.copy2(SRC,APP)
text=APP.read_text(encoding='utf-8')

# helpers: insert immediately before render_positions using AST line number
mod=ast.parse(text)
funcs={n.name:n for n in mod.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
rp=funcs.get('render_positions')
if not rp:
    print('ABORT=RENDER_POSITIONS_NOT_FOUND'); shutil.copy2(app_bak,APP); sys.exit(4)

helpers="""\n# V80_US_LIVE_CONSOLE_BRIDGE\ndef _v80_json_file(path, default):\n    import json\n    try:\n        return json.loads(Path(path).read_text(encoding='utf-8'))\n    except Exception:\n        return default\n\ndef v80_us_account():\n    d=_v80_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_account.json',{})\n    return d if isinstance(d,dict) else {}\n\ndef v80_us_eval_rows():\n    d=_v80_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})\n    if isinstance(d,list): return d\n    if isinstance(d,dict) and isinstance(d.get('rows'),list): return d.get('rows')\n    return []\n\ndef v80_us_trade_enabled():\n    p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n    if not p.exists(): return True\n    try: return p.read_text(encoding='utf-8').strip()!='0'\n    except Exception: return True\n\ndef v80_set_us_trade_enabled(enabled):\n    Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag').write_text('1' if enabled else '0',encoding='utf-8')\n\n"""
lines=text.splitlines(True)
if 'V80_US_LIVE_CONSOLE_BRIDGE' not in text:
    lines.insert(rp.lineno-1,helpers)
    text=''.join(lines)

# reparses after helper insertion and inject USA branch as first function statements by line
mod=ast.parse(text)
rp=next((n for n in mod.body if isinstance(n,ast.FunctionDef) and n.name=='render_positions'),None)
if not rp or not rp.body:
    print('ABORT=RENDER_POSITIONS_BODY_MISSING'); shutil.copy2(app_bak,APP); sys.exit(5)
insert_line=rp.body[0].lineno-1
branch="""    if market=='USA':\n        acct=v80_us_account()\n        hs=acct.get('holdings') if isinstance(acct,dict) else []\n        hs=hs if isinstance(hs,list) else []\n        st.markdown('<div class=\"v5-section-title\">🛡 실제 Kiwoom US 보유주식</div>',unsafe_allow_html=True)\n        if not hs:\n            st.info('현재 Kiwoom US 모의계좌 보유종목이 없습니다.')\n            return\n        import pandas as pd\n        out=[]\n        for h in hs:\n            if not isinstance(h,dict): continue\n            out.append({'종목':h.get('symbol') or h.get('stk_cd') or '-', '수량':h.get('qty') or h.get('hold_qty') or 0, '평균가':h.get('avg') or h.get('frgn_stk_book_uv') or 0, '현재가':h.get('price') or h.get('now_pric') or 0, '평가액':h.get('market_value') or h.get('evlt_amt') or 0, '손익':h.get('pnl') or h.get('pl_amt') or 0, '손익률%':h.get('pnl_pct') or h.get('pl_rt') or 0})\n        st.dataframe(pd.DataFrame(out),use_container_width=True,hide_index=True)\n        return\n"""
if "acct=v80_us_account()" not in text:
    lines=text.splitlines(True); lines.insert(insert_line,branch); text=''.join(lines)

# Finder/session direct bridge: exact one-line anchor only
needle="status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)"
if needle in text and '_v80_rows=v80_us_eval_rows()' not in text:
    repl=needle+"\n    if market=='USA':\n        _v80_rows=v80_us_eval_rows()\n        if _v80_rows:\n            finders=_v80_rows[:20]\n            trackers=_v80_rows\n            _v80_eval=_v80_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})\n            if isinstance(_v80_eval,dict) and _v80_eval.get('session'):\n                status=dict(status or {}); status['session']=_v80_eval.get('session')"
    text=text.replace(needle,repl,1)
elif '_v80_rows=v80_us_eval_rows()' not in text:
    print('ABORT=STATUS_ANCHOR_MISSING'); shutil.copy2(app_bak,APP); sys.exit(6)

# visible switch before known US account assignment; preserve indentation from line
marker='_ua=v45_us_live_account(); total'
if 'V80 실제 자동매매' not in text:
    idx=text.find(marker)
    if idx<0:
        print('ABORT=US_ACCOUNT_ANCHOR_MISSING'); shutil.copy2(app_bak,APP); sys.exit(7)
    ls=text.rfind('\n',0,idx)+1
    indent=re.match(r'[ \t]*',text[ls:idx]).group(0)
    block=(indent+"_v80_trade=v80_us_trade_enabled()\n"+
           indent+"_v80_new=st.toggle('V80 실제 자동매매',value=_v80_trade,key='v80_us_trade_toggle') if market=='USA' else _v80_trade\n"+
           indent+"if market=='USA' and _v80_new!=_v80_trade:\n"+
           indent+"    v80_set_us_trade_enabled(_v80_new); st.rerun()\n")
    text=text[:ls]+block+text[ls:]

APP.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
if r.returncode:
    print('PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(app_bak,APP); print('APP_ROLLBACK=YES'); sys.exit(8)
print('PY_COMPILE=PASS')

# engine guard: no strategy change, only actual-order switch
eng=ENGINE.read_text(encoding='utf-8')
eng_bak=ENGINE.with_suffix('.py.pre_v80')
if 'V80_US_TRADE_SWITCH_GUARD' not in eng:
    target='def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n'
    if target not in eng:
        print('ABORT=ENGINE_ORDER_ONCE_NOT_FOUND'); shutil.copy2(app_bak,APP); sys.exit(9)
    guard="""# V80_US_TRADE_SWITCH_GUARD\ndef _v80_trade_enabled():\n    try:\n        p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n        return (not p.exists()) or p.read_text(encoding='utf-8').strip()!='0'\n    except Exception:\n        return True\n\n"""
    eng=eng.replace(target,guard+target,1)
    pos=eng.index(target)+len(target)
    eng=eng[:pos]+"    if not _v80_trade_enabled():\n        log('ORDER_BLOCKED_TRADE_SWITCH_OFF',side=side,symbol=sym,qty=qty,reason=reason)\n        return {'ok':False,'reason':'TRADE_SWITCH_OFF'}\n"+eng[pos:]
    shutil.copy2(ENGINE,eng_bak)
    ENGINE.write_text(eng,encoding='utf-8')
    rr=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
    if rr.returncode:
        print('ENGINE_PY_COMPILE=FAIL'); print((rr.stderr or rr.stdout).strip()); shutil.copy2(eng_bak,ENGINE); shutil.copy2(app_bak,APP); print('ROLLBACK=YES'); sys.exit(10)
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
    print('V5_HTTP=FAIL',repr(e)); sys.exit(11)
print('APP_BASE=PRE_V76_COMPILE_PASS')
print('US_ACCOUNT_SOURCE=KIWOOM_MOCK_ACCOUNT_SNAPSHOT')
print('US_POSITIONS_SOURCE=KIWOOM_ACCOUNT_FILE')
print('FINDER_SOURCE=V22E_LIVE_EVAL_DIRECT')
print('TRADE_SWITCH=V5_TO_ENGINE_ORDER_GUARD')
print('KR_PATH=UNCHANGED')
print('DEPLOY=PASS')
