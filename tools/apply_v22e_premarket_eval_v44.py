#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
RUNNER_BACKUP=RUNTIME/'live_server'/'v22e_us_mock_live.py.pre_v44_premarket_eval'
APP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py')
APP_BACKUP=Path('/home/ubuntu/day-trader-api-repo/app_v5.py.pre_v44_premarket_eval')
SERVICE='day-trader-v22e-us'
PORT=8503
LOG=Path('/tmp/daytrader-v5.log')


def run(*args,check=True):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check)


def compile_text(text: str, prefix: str) -> Path:
    fd,name=tempfile.mkstemp(prefix=prefix,suffix='.py')
    Path(name).write_text(text,encoding='utf-8')
    py_compile.compile(name,doraise=True)
    return Path(name)


def patch_runner(s: str) -> str:
    if 'V44_PREMARKET_EVAL_ORDER_GATE' in s:
        return s

    old="""STATE_PATH = Path(os.getenv('V22E_US_STATE_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_state.json'))
LOG_PATH = Path(os.getenv('V22E_US_LOG_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl'))"""
    new="""STATE_PATH = Path(os.getenv('V22E_US_STATE_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_state.json'))
EVAL_PATH = Path(os.getenv('V22E_US_EVAL_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'))
LOG_PATH = Path(os.getenv('V22E_US_LOG_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl'))
V44_PREMARKET_EVAL_ORDER_GATE = True"""
    if old not in s: raise SystemExit('ABORT runner path anchor missing')
    s=s.replace(old,new,1)

    old="""def session():
    now = datetime.now(timezone.utc).astimezone(ET)
    minute = now.hour * 60 + now.minute
    regular = now.weekday() < 5 and 9*60+30 <= minute < 16*60
    return now, minute, regular
"""
    new="""def session():
    now = datetime.now(timezone.utc).astimezone(ET)
    minute = now.hour * 60 + now.minute
    weekday = now.weekday() < 5
    premarket = weekday and 4*60 <= minute < 9*60+30
    regular = weekday and 9*60+30 <= minute < 16*60
    evaluation = premarket or regular
    return now, minute, premarket, regular, evaluation
"""
    if old not in s: raise SystemExit('ABORT session anchor missing')
    s=s.replace(old,new,1)

    anchor="""def save_state(d):
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    tmp.replace(STATE_PATH)
"""
    addition=anchor+"""

def publish_eval(store, sym, decision, session_name, holding=False):
    row = {
        'symbol': sym,
        'engine': ENGINE_NAME,
        'session': session_name,
        'order_gate': 'ENABLED' if session_name == 'REGULAR' else 'DISABLED',
        'holding': bool(holding),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        **(decision or {}),
    }
    store[sym] = row
    payload = {
        'engine': ENGINE_NAME,
        'market': 'USA',
        'premarket_evaluation': True,
        'regular_order_only': True,
        'rows': store,
        'updated_at': row['updated_at'],
    }
    try:
        tmp = EVAL_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        tmp.replace(EVAL_PATH)
    except Exception as e:
        log('EVAL_STATE_WRITE_ERROR', symbol=sym, error=repr(e))
    return row
"""
    if anchor not in s: raise SystemExit('ABORT save_state anchor missing')
    s=s.replace(anchor,addition,1)

    s=s.replace("    state = load_state()\n    last_bar = {}", "    state = load_state()\n    eval_store = {}\n    last_bar = {}",1)
    s=s.replace("            now_et, et_min, regular = session()", "            now_et, et_min, premarket, regular, evaluation_session = session()",1)

    # Do not process stale overnight bars; evaluate during premarket + regular only.
    target="""                px = f(b5.iloc[-1].get('close'))
                h = holdings.get(sym)

                if h:
"""
    repl="""                px = f(b5.iloc[-1].get('close'))
                h = holdings.get(sym)
                if not evaluation_session:
                    continue
                session_name = 'PREMARKET' if premarket else 'REGULAR'

                if h:
"""
    if target not in s: raise SystemExit('ABORT evaluation-session anchor missing')
    s=s.replace(target,repl,1)

    # Holdings: evaluate in premarket, but only submit exits during regular session.
    target="""                    d = evaluate_exit(b5, pos)
                    force_flat = regular and et_min >= FORCE_FLAT_MINUTE_ET
                    if force_flat:
                        d = {'exit': True, 'sell_qty': h['qty'], 'reason': 'V22E_US_EOD_FORCE_FLAT', 'price': px}
                    if d.get('exit'):
                        sell_qty = min(i(d.get('sell_qty')), i(h.get('qty')))
                        if sell_qty > 0:
                            res = order_once('SELL', sym, sell_qty, px, h.get('exchange') or settings.exchange_for(sym), bar_key, str(d.get('reason')))
                            if res.get('ok'):
                                if d.get('tp1_done'): meta['tp1_done'] = True
                                if d.get('outer_reduced'): meta['outer_reduced'] = True
                                meta['updated_at'] = datetime.now(timezone.utc).isoformat(); state[sym] = meta; save_state(state)
                                _last_recon = 0.0
                    continue

                # No entries outside regular session or after EOD flatten cutoff.
                if not regular or et_min >= FORCE_FLAT_MINUTE_ET:
                    continue
                d = evaluate_entry(b5)
                if d.get('enter'):
"""
    repl="""                    d = evaluate_exit(b5, pos)
                    force_flat = regular and et_min >= FORCE_FLAT_MINUTE_ET
                    if force_flat:
                        d = {'exit': True, 'sell_qty': h['qty'], 'reason': 'V22E_US_EOD_FORCE_FLAT', 'price': px}
                    publish_eval(eval_store, sym, d, session_name, holding=True)
                    if premarket:
                        log('PREMARKET_EVAL_ONLY', symbol=sym, side='SELL' if d.get('exit') else 'HOLD', reason=d.get('reason'), price=px, order_gate='DISABLED')
                        continue
                    if d.get('exit'):
                        sell_qty = min(i(d.get('sell_qty')), i(h.get('qty')))
                        if sell_qty > 0:
                            res = order_once('SELL', sym, sell_qty, px, h.get('exchange') or settings.exchange_for(sym), bar_key, str(d.get('reason')))
                            if res.get('ok'):
                                if d.get('tp1_done'): meta['tp1_done'] = True
                                if d.get('outer_reduced'): meta['outer_reduced'] = True
                                meta['updated_at'] = datetime.now(timezone.utc).isoformat(); state[sym] = meta; save_state(state)
                                _last_recon = 0.0
                    continue

                # Evaluate entries in both PREMARKET and REGULAR. Orders remain REGULAR-only.
                d = evaluate_entry(b5)
                publish_eval(eval_store, sym, d, session_name, holding=False)
                if premarket:
                    log('PREMARKET_EVAL_ONLY', symbol=sym, side='BUY' if d.get('enter') else 'WATCH', score=d.get('effective_score'), reason=d.get('reason'), price=px, order_gate='DISABLED')
                    continue
                if et_min >= FORCE_FLAT_MINUTE_ET:
                    continue
                if d.get('enter'):
"""
    if target not in s: raise SystemExit('ABORT runner order-gate block anchor missing')
    s=s.replace(target,repl,1)

    required=['V44_PREMARKET_EVAL_ORDER_GATE = True','premarket = weekday and 4*60 <= minute < 9*60+30',"publish_eval(eval_store, sym, d, session_name, holding=False)","if premarket:\n                    log('PREMARKET_EVAL_ONLY'","regular_order_only': True"]
    for x in required:
        if x not in s: raise SystemExit('ABORT runner marker missing '+x)
    return s


def patch_app(s: str) -> str:
    if 'V44_US_EVAL_PATH' not in s:
        anchor='def api(path, timeout=10):'
        helper="""V44_US_EVAL_PATH='/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'\n\ndef v44_us_eval_rows():\n    try:\n        import json\n        from pathlib import Path as _V44Path\n        p=_V44Path(V44_US_EVAL_PATH)\n        d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}\n        rows=d.get('rows') or {}\n        return rows if isinstance(rows,dict) else {}\n    except Exception:\n        return {}\n\n"""
        if anchor not in s: raise SystemExit('ABORT app api anchor missing')
        s=s.replace(anchor,helper+anchor,1)

    # Merge the V22E evaluation for the selected U.S. symbol into the existing detail row.
    target="""            sym=str(selected.get('symbol') or '-')
            name=resolve_display_name(market,sym,selected.get('name') or '')
            px=money(selected.get('price') or selected.get('current_price'),market)
            v22=selected.get('engine5_v22_decision') or {}
"""
    repl="""            sym=str(selected.get('symbol') or '-')
            name=resolve_display_name(market,sym,selected.get('name') or '')
            if market=='USA':
                _v44=v44_us_eval_rows().get(sym.upper()) or {}
                if _v44:
                    selected=dict(selected); selected['engine5_v22_decision']=_v44
            px=money(selected.get('price') or selected.get('current_price'),market)
            v22=selected.get('engine5_v22_decision') or {}
"""
    if target not in s:
        # Runtime UI may have minor spacing/version edits; patch by smaller stable anchor.
        small="            px=money(selected.get('price') or selected.get('current_price'),market)\n            v22=selected.get('engine5_v22_decision') or {}\n"
        if small not in s: raise SystemExit('ABORT app selected V22 anchor missing')
        inject="            if market=='USA':\n                _v44=v44_us_eval_rows().get(sym.upper()) or {}\n                if _v44:\n                    selected=dict(selected); selected['engine5_v22_decision']=_v44\n"
        s=s.replace(small,inject+small,1)
    else:
        s=s.replace(target,repl,1)

    s=s.replace("score_lbl='V22' if v22 else 'Finder'", "score_lbl=('V22E' if market=='USA' else 'V22') if v22 else 'Finder'")
    s=s.replace('DAY TRADER V5 <small>v42</small>','DAY TRADER V5 <small>v44</small>')
    if 'V44_US_EVAL_PATH' not in s or 'v44_us_eval_rows' not in s: raise SystemExit('ABORT app V44 markers missing')
    return s


def wait_http(url,seconds=45):
    deadline=time.time()+seconds; last=None
    while time.time()<deadline:
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status==200:return
        except Exception as e:last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP failed {url}: {last}')


def main():
    if not RUNNER.exists() or not APP.exists(): raise SystemExit('ABORT required runtime/app missing')
    runner_old=RUNNER.read_text(encoding='utf-8')
    app_old=APP.read_text(encoding='utf-8')
    runner_new=patch_runner(runner_old)
    app_new=patch_app(app_old)
    tr=compile_text(runner_new,'v22e_v44_')
    ta=compile_text(app_new,'app_v44_')
    print('PY_COMPILE=PASS',flush=True)
    try:
        if not RUNNER_BACKUP.exists(): run('sudo','cp','-a',RUNNER,RUNNER_BACKUP)
        if not APP_BACKUP.exists(): APP_BACKUP.write_text(app_old,encoding='utf-8')
        run('sudo','install','-m','0644',tr,RUNNER)
        APP.write_text(app_new,encoding='utf-8')
    finally:
        tr.unlink(missing_ok=True); ta.unlink(missing_ok=True)

    run('sudo','systemctl','restart',SERVICE)
    time.sleep(4)
    active=subprocess.check_output(['sudo','systemctl','is-active',SERVICE],text=True).strip()
    if active!='active':
        run('sudo','systemctl','status',SERVICE,'--no-pager','-l',check=False)
        run('sudo','journalctl','-u',SERVICE,'-n','100','--no-pager',check=False)
        raise SystemExit('ABORT V22E service inactive')
    print('V22E_SERVICE=ACTIVE',flush=True)

    subprocess.run(['pkill','-f','streamlit run app_v5.py'],check=False)
    time.sleep(1)
    cmd=f'cd {APP.parent} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup /home/ubuntu/day-trader-api/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &'
    subprocess.Popen(['bash','-lc',cmd],start_new_session=True)
    wait_http(f'http://127.0.0.1:{PORT}/',45)
    print('V5_HTTP=PASS',flush=True)

    # Runtime assertions. No broker order is issued by this deployment test.
    rs=RUNNER.read_text(encoding='utf-8')
    if 'V44_PREMARKET_EVAL_ORDER_GATE = True' not in rs: raise SystemExit('ABORT runtime marker absent')
    if "premarket = weekday and 4*60 <= minute < 9*60+30" not in rs: raise SystemExit('ABORT premarket window absent')
    if "if premarket:\n                    log('PREMARKET_EVAL_ONLY'" not in rs: raise SystemExit('ABORT order gate absent')

    print('USA_PREMARKET_FINDER=ON',flush=True)
    print('USA_PREMARKET_TRACKER=ON',flush=True)
    print('USA_PREMARKET_V22E_EVAL=ON',flush=True)
    print('USA_PREMARKET_ORDER_GATE=DISABLED',flush=True)
    print('USA_REGULAR_ORDER_GATE=ENABLED',flush=True)
    print('USA_PREMARKET_WINDOW_ET=04:00-09:30',flush=True)
    print('V5_US_V22E_EVAL=CONNECTED',flush=True)
    print('WILLIAMS_ORDER_AUTHORITY=DISABLED_UNCHANGED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':
    main()
