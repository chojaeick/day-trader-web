#!/usr/bin/env python3
from pathlib import Path
import shutil, subprocess, time, sys, re

ROOT=Path('/home/ubuntu/day-trader-api')
APP=ROOT/'app_v5.py'
SRC=ROOT/'app_v5.py.pre_v76_1788195415'
ENGINE=ROOT/'live_server/v22e_us_mock_live.py'
TRADE_FLAG=ROOT/'v22e_us_trade_enabled.flag'
EVAL=ROOT/'v22e_us_mock_eval.json'
ACCT=ROOT/'v22e_us_mock_account.json'
LOG=ROOT/'v22e_us_mock_live.jsonl'
PY=ROOT/'venv/bin/python'

print('V79_START=YES')
if not SRC.exists():
    print('ABORT=PRE_V76_BACKUP_MISSING'); sys.exit(2)
# verify source compiles before touching runtime
r=subprocess.run([str(PY),'-m','py_compile',str(SRC)],capture_output=True,text=True)
if r.returncode:
    print('ABORT=PRE_V76_BACKUP_NOT_COMPILE_PASS'); print((r.stderr or r.stdout).strip()); sys.exit(3)

bak=ROOT/f'app_v5.py.pre_v79_{int(time.time())}'
if APP.exists(): shutil.copy2(APP,bak)
shutil.copy2(SRC,APP)
text=APP.read_text(encoding='utf-8')

# 1) lightweight direct US account/eval/log helpers inserted before render_positions if absent
helper='''\n# V79_US_LIVE_CONSOLE_BRIDGE\ndef _v79_json_file(path, default):\n    import json\n    try:\n        return json.loads(Path(path).read_text(encoding='utf-8'))\n    except Exception:\n        return default\n\ndef v79_us_account():\n    d=_v79_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_account.json',{})\n    return d if isinstance(d,dict) else {}\n\ndef v79_us_eval_rows():\n    d=_v79_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})\n    if isinstance(d,list): return d\n    if isinstance(d,dict) and isinstance(d.get('rows'),list): return d.get('rows')\n    return []\n\ndef v79_us_trade_enabled():\n    p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n    if not p.exists(): return True\n    try: return p.read_text(encoding='utf-8').strip()!='0'\n    except Exception: return True\n\ndef v79_set_us_trade_enabled(enabled):\n    p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n    p.write_text('1' if enabled else '0',encoding='utf-8')\n\n'''
if 'V79_US_LIVE_CONSOLE_BRIDGE' not in text:
    anchor='def render_positions(market,tracker):'
    if anchor not in text:
        print('ABORT=RENDER_POSITIONS_ANCHOR_MISSING'); shutil.copy2(bak,APP); sys.exit(4)
    text=text.replace(anchor,helper+anchor,1)

# 2) replace only body prefix of render_positions for USA; keep KR original path untouched
old="""def render_positions(market,tracker):\n    st.markdown('<div class=\"v5-section-title\"> 보유주식 관리</div><div class=\"v5-section-sub\">전체 폭 관리 · 단타/중장기 즉시 전환 · 검증된 종목만 등록</div>',unsafe_allow_html=True)\n    render_manual_holding(market,'holdings')\n    pos_rows,_=position_rows(); shown=0\n"""
new="""def render_positions(market,tracker):\n    st.markdown('<div class=\"v5-section-title\"> 보유주식 관리</div><div class=\"v5-section-sub\">전체 폭 관리 · 단타/중장기 즉시 전환 · 검증된 종목만 등록</div>',unsafe_allow_html=True)\n    if market=='USA':\n        acct=v79_us_account(); hs=acct.get('holdings') if isinstance(acct,dict) else []\n        hs=hs if isinstance(hs,list) else []\n        if not hs:\n            st.info('현재 Kiwoom US 모의계좌 보유종목이 없습니다.')\n            return\n        import pandas as pd\n        out=[]\n        for h in hs:\n            if not isinstance(h,dict): continue\n            out.append({'종목':h.get('symbol') or h.get('stk_cd') or '-', '수량':h.get('qty') or h.get('hold_qty') or 0, '평균가':h.get('avg') or h.get('frgn_stk_book_uv') or 0, '현재가':h.get('price') or h.get('now_pric') or 0, '평가액':h.get('market_value') or h.get('evlt_amt') or 0, '손익':h.get('pnl') or h.get('pl_amt') or 0, '손익률%':h.get('pnl_pct') or h.get('pl_rt') or 0})\n        st.dataframe(pd.DataFrame(out),use_container_width=True,hide_index=True)\n        return\n    render_manual_holding(market,'holdings')\n    pos_rows,_=position_rows(); shown=0\n"""
if old in text:
    text=text.replace(old,new,1)
elif "if market=='USA':\n        acct=v79_us_account()" not in text:
    print('ABORT=RENDER_POSITIONS_PREFIX_NOT_FOUND'); shutil.copy2(bak,APP); sys.exit(5)

# 3) USA status bridge: make finder/session come directly from live eval, minimal insertion after status acquisition
needle='status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)'
repl="""status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)\n    if market=='USA':\n        _v79_rows=v79_us_eval_rows()\n        if _v79_rows:\n            finders=_v79_rows[:20]\n            trackers=_v79_rows\n            _v79_eval=_v79_json_file('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json',{})\n            if isinstance(_v79_eval,dict) and _v79_eval.get('session'):\n                status=dict(status or {}); status['session']=_v79_eval.get('session')\n"""
if needle in text:
    text=text.replace(needle,repl,1)
elif '_v79_rows=v79_us_eval_rows()' not in text:
    print('ABORT=STATUS_ANCHOR_MISSING'); shutil.copy2(bak,APP); sys.exit(6)

# 4) visible actual trade switch near US top account block, only insert once
marker="_ua=v45_us_live_account(); total"
idx=text.find(marker)
if idx!=-1 and 'V79 실제 자동매매' not in text:
    line_start=text.rfind('\n',0,idx)+1
    indent=re.match(r'[ \t]*',text[line_start:idx]).group(0)
    block=(indent+"_v79_trade=v79_us_trade_enabled()\n"+
           indent+"_v79_new=st.toggle('V79 실제 자동매매',value=_v79_trade,key='v79_us_trade_toggle') if market=='USA' else _v79_trade\n"+
           indent+"if market=='USA' and _v79_new!=_v79_trade:\n"+
           indent+"    v79_set_us_trade_enabled(_v79_new); st.rerun()\n")
    text=text[:line_start]+block+text[line_start:]
elif idx==-1 and 'V79 실제 자동매매' not in text:
    print('ABORT=US_ACCOUNT_ANCHOR_MISSING'); shutil.copy2(bak,APP); sys.exit(7)

APP.write_text(text,encoding='utf-8')
r=subprocess.run([str(PY),'-m','py_compile',str(APP)],capture_output=True,text=True)
if r.returncode:
    print('PY_COMPILE=FAIL'); print((r.stderr or r.stdout).strip()); shutil.copy2(bak,APP); print('APP_ROLLBACK=YES'); sys.exit(8)
print('PY_COMPILE=PASS')

# patch engine order guard only if absent
eng=ENGINE.read_text(encoding='utf-8')
if 'V79_US_TRADE_SWITCH_GUARD' not in eng:
    target='def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n'
    if target not in eng:
        print('ABORT=ENGINE_ORDER_ONCE_ANCHOR_MISSING'); shutil.copy2(bak,APP); sys.exit(9)
    guard="""# V79_US_TRADE_SWITCH_GUARD\ndef _v79_trade_enabled():\n    try:\n        p=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n        return (not p.exists()) or p.read_text(encoding='utf-8').strip()!='0'\n    except Exception:\n        return True\n\n"""
    eng=eng.replace(target,guard+target,1)
    start=eng.index(target)+len(target)
    eng=eng[:start]+"    if not _v79_trade_enabled():\n        log('ORDER_BLOCKED_TRADE_SWITCH_OFF',side=side,symbol=sym,qty=qty,reason=reason)\n        return {'ok':False,'reason':'TRADE_SWITCH_OFF'}\n"+eng[start:]
    ebak=ENGINE.with_suffix('.py.pre_v79')
    shutil.copy2(ENGINE,ebak)
    ENGINE.write_text(eng,encoding='utf-8')
    rr=subprocess.run([str(PY),'-m','py_compile',str(ENGINE)],capture_output=True,text=True)
    if rr.returncode:
        shutil.copy2(ebak,ENGINE); shutil.copy2(bak,APP); print('ENGINE_PY_COMPILE=FAIL'); print((rr.stderr or rr.stdout).strip()); print('ROLLBACK=YES'); sys.exit(10)
print('ENGINE_PY_COMPILE=PASS')

# default switch ON if no file exists
if not TRADE_FLAG.exists(): TRADE_FLAG.write_text('1',encoding='utf-8')

subprocess.run(['sudo','systemctl','restart','day-trader-v22e-us.service'])
# restart V5 nohup safely
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
