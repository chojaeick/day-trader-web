#!/usr/bin/env python3
from pathlib import Path
import os, re, shutil, subprocess, time

ENGINE=Path('/home/ubuntu/day-trader-api/live_server/v22e_us_mock_live.py')
APP=Path('/home/ubuntu/day-trader-api/app_v5.py')
SWITCH=Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')


def backup(p):
    if p.exists():
        q=p.with_name(p.name+f'.pre_v76_{int(time.time())}')
        shutil.copy2(p,q)
        return q


def patch_engine():
    s=ENGINE.read_text(encoding='utf-8')
    backup(ENGINE)
    if 'V76_US_RUNTIME_RECONCILE = True' not in s:
        anchor='V57_USD_ONLY_CASH_PARSE = True'
        if anchor not in s: raise SystemExit('ABORT engine marker missing')
        s=s.replace(anchor, anchor+"\nV76_US_RUNTIME_RECONCILE = True\nTRADE_SWITCH_PATH = Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')\n\ndef trade_enabled():\n    try:\n        return TRADE_SWITCH_PATH.read_text(encoding='utf-8').strip().upper() in {'1','ON','TRUE','YES','ENABLE','ENABLED'}\n    except Exception:\n        return True\n",1)
    # Hard guard at broker order boundary.
    needle="def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):\n"
    if needle in s and 'ORDER_BLOCKED_TRADE_SWITCH_OFF' not in s:
        repl=needle+"    if not trade_enabled():\n        log('ORDER_BLOCKED_TRADE_SWITCH_OFF', side=side, symbol=sym, qty=qty, reason=reason)\n        return {'ok': False, 'reason': 'TRADE_SWITCH_OFF'}\n"
        s=s.replace(needle,repl,1)
    # Reconcile every live broker holding into engine state immediately after a valid holdings refresh.
    marker='holdings = refresh_holdings()'
    if marker in s and 'V76_RECONCILED_FROM_KIWOOM' not in s:
        inject=marker+"\n            # V76: broker account is source of truth. Register every real holding for management.\n            for _v76_sym, _v76_h in (holdings or {}).items():\n                if _v76_sym not in state:\n                    _v76_entry=f(_v76_h.get('avg')) or f(_v76_h.get('price'))\n                    state[_v76_sym]={'symbol':_v76_sym,'entry_price':_v76_entry,'qty':i(_v76_h.get('qty')),'exchange':_v76_h.get('exchange') or resolve_exchange(_v76_sym),'reconciled_from_broker':True}\n                    save_state(state)\n                    log('V76_RECONCILED_FROM_KIWOOM',symbol=_v76_sym,qty=i(_v76_h.get('qty')),entry_price=_v76_entry)\n"
        s=s.replace(marker,inject,1)
    ENGINE.write_text(s,encoding='utf-8')


def patch_app():
    s=APP.read_text(encoding='utf-8')
    backup(APP)
    if 'V76_US_RUNTIME_CONSOLE = True' not in s:
        # Add helpers before render_positions; keep existing layout machinery intact.
        anchor='def render_positions(market,tracker):'
        if anchor not in s: raise SystemExit('ABORT render_positions missing')
        helper=r'''V76_US_RUNTIME_CONSOLE = True
V76_US_TRADE_SWITCH = Path('/home/ubuntu/day-trader-api/v22e_us_trade_enabled.flag')
V76_US_EVAL_PATH = Path('/home/ubuntu/day-trader-api/v22e_us_mock_eval.json')
V76_US_LOG_PATH = Path('/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl')

def v76_trade_enabled():
    try: return V76_US_TRADE_SWITCH.read_text(encoding='utf-8').strip().upper() in {'1','ON','TRUE','YES','ENABLE','ENABLED'}
    except Exception: return True

def v76_set_trade_enabled(on):
    try:
        V76_US_TRADE_SWITCH.write_text('ON' if on else 'OFF',encoding='utf-8')
        return True
    except Exception: return False

def v76_eval_rows():
    try:
        d=json.loads(V76_US_EVAL_PATH.read_text(encoding='utf-8'))
        if isinstance(d,list): return d
        if isinstance(d,dict) and isinstance(d.get('rows'),list): return d.get('rows')
    except Exception: pass
    return []

def v76_recent_orders(limit=12):
    out=[]
    try:
        for line in V76_US_LOG_PATH.read_text(encoding='utf-8',errors='ignore').splitlines()[-400:]:
            try: x=json.loads(line)
            except Exception: continue
            if str(x.get('event') or '').startswith(('ORDER_','STALE_ORDER_','BUY_','SELL_')):
                out.append(x)
    except Exception: pass
    return out[-limit:][::-1]

def v76_render_us_console():
    acct=v45_us_live_account() if 'v45_us_live_account' in globals() else {}
    rows=v76_eval_rows()
    st.markdown('### 🇺🇸 US V22E 실시간 운용')
    a,b,c,d=st.columns([1.1,1,1,1])
    on=v76_trade_enabled()
    a.metric('자동매매', 'ON' if on else 'OFF')
    if b.button('▶ 자동매매 ON',use_container_width=True,key='v76_us_on'):
        v76_set_trade_enabled(True); st.rerun()
    if c.button('■ 자동매매 OFF',use_container_width=True,key='v76_us_off'):
        v76_set_trade_enabled(False); st.rerun()
    d.metric('Finder',f"{len(rows)} 종목")
    hs=(acct or {}).get('holdings') or []
    c1,c2,c3,c4=st.columns(4)
    c1.metric('총자산', money((acct or {}).get('total_assets'),'USA'))
    c2.metric('현금/주문가능', money((acct or {}).get('orderable_cash') or (acct or {}).get('cash'),'USA'))
    c3.metric('주식평가', money((acct or {}).get('stock_value'),'USA'))
    c4.metric('실제 보유', f"{len(hs)} 종목")
    if hs:
        show=[]
        for h in hs:
            show.append({'종목':h.get('symbol'),'수량':h.get('qty'),'평균가':h.get('avg'),'현재가':h.get('price'),'평가액':h.get('market_value'),'손익':h.get('pnl'),'수익률%':h.get('pnl_pct')})
        st.dataframe(show,use_container_width=True,hide_index=True)
    else: st.info('Kiwoom US mock 계좌 보유종목 없음')
    if rows:
        rr=sorted(rows,key=lambda x: float(x.get('score') or x.get('finder_score') or 0),reverse=True)[:20]
        show=[]
        for x in rr:
            show.append({'종목':x.get('symbol'),'점수':round(float(x.get('score') or x.get('finder_score') or 0),1),'진입':bool(x.get('enter')),'판단':x.get('reason') or x.get('signal') or '-','보유':bool(x.get('holding'))})
        st.markdown('#### Finder TOP 20')
        st.dataframe(show,use_container_width=True,hide_index=True,height=min(600,38*(len(show)+1)))
    ev=v76_recent_orders()
    if ev:
        st.markdown('#### 최근 주문 · 취소')
        show=[]
        for x in ev:
            show.append({'시간':str(x.get('ts') or '')[11:19],'이벤트':x.get('event'),'매수/매도':x.get('side'),'종목':x.get('symbol'),'수량':x.get('qty'),'가격':x.get('limit'),'사유':x.get('reason'),'주문번호':x.get('ord_no')})
        st.dataframe(show,use_container_width=True,hide_index=True)

'''
        s=s.replace(anchor,helper+anchor,1)
    # Make USA positions broker-native and never show the stale internal ledger.
    old="def render_positions(market,tracker):\n    st.markdown('<div class=\"v5-section-title\">"
    if old in s and 'V76_BROKER_NATIVE_POSITIONS' not in s:
        new="def render_positions(market,tracker):\n    # V76_BROKER_NATIVE_POSITIONS: US holdings come only from Kiwoom broker snapshot.\n    if market=='USA':\n        v76_render_us_console()\n        return\n    st.markdown('<div class=\"v5-section-title\">"
        s=s.replace(old,new,1)
    # Ensure USA status/candidates do not depend on stale API bridge if eval file is alive.
    target="status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)"
    if target in s and 'V76_US_DIRECT_EVAL' not in s:
        repl="status=get_market_status(market); finders=finder_rows(status); trackers=tracker_rows(status)\n    # V76_US_DIRECT_EVAL\n    if market=='USA':\n        _v76_rows=v76_eval_rows()\n        if _v76_rows:\n            finders=_v76_rows\n            trackers=_v76_rows"
        s=s.replace(target,repl,1)
    APP.write_text(s,encoding='utf-8')


def run(cmd):
    return subprocess.run(cmd,shell=True,text=True,capture_output=True)

if __name__=='__main__':
    if not SWITCH.exists(): SWITCH.write_text('ON',encoding='utf-8')
    os.chmod(SWITCH,0o666)
    patch_engine(); patch_app()
    r=run('/home/ubuntu/day-trader-api/venv/bin/python -m py_compile '+str(ENGINE)+' '+str(APP))
    if r.returncode:
        print(r.stdout); print(r.stderr); raise SystemExit('PY_COMPILE=FAIL')
    print('PY_COMPILE=PASS')
    run('sudo systemctl restart day-trader-v22e-us.service')
    # restart V5 only; preserve API/KR
    run("pkill -f 'streamlit run /home/ubuntu/day-trader-api/app_v5.py' || true")
    time.sleep(1)
    run("cd /home/ubuntu/day-trader-api && nohup /home/ubuntu/day-trader-api/venv/bin/streamlit run app_v5.py --server.address 0.0.0.0 --server.port 8503 > /home/ubuntu/day-trader-api/app_v5.log 2>&1 &")
    time.sleep(4)
    print('US_ACCOUNT_SOURCE=KIWOOM_MOCK_ACCOUNT_SNAPSHOT')
    print('US_HOLDINGS_SOURCE=KIWOOM_MOCK_ACCOUNT_SNAPSHOT')
    print('ENGINE_START_RECONCILE=ALL_BROKER_HOLDINGS')
    print('CAPITAL_ALLOCATOR=LIVE_ACCOUNT_EXISTING_V58_99_5PCT')
    print('TRADE_SWITCH=V5_BUTTON_TO_ENGINE_ORDER_GUARD')
    print('FINDER_SOURCE=V22E_LIVE_EVAL_DIRECT')
    print('HISTORY=RECENT_ORDER_CANCEL_EVENTS_VISIBLE')
    print('V22E_SERVICE='+('ACTIVE' if run('systemctl is-active day-trader-v22e-us.service').stdout.strip()=='active' else 'NOT_ACTIVE'))
    h=run("curl -fsS --max-time 8 http://127.0.0.1:8503/ >/dev/null")
    print('V5_HTTP='+('PASS' if h.returncode==0 else 'FAIL'))
    print('DEPLOY=PASS' if h.returncode==0 else 'DEPLOY=PARTIAL')
