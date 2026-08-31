#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, re, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
BROKER=RUNTIME/'live_server'/'kiwoom_us_mock_broker.py'
V4=RUNTIME/'live_server'/'v4_engine.py'
API=RUNTIME/'live_server'/'api.py'
APP=REPO/'app_v5.py'
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503


def run(*args,check=True,capture=False):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check,text=True,capture_output=capture)


def compile_text(text,prefix):
    fd,name=tempfile.mkstemp(prefix=prefix,suffix='.py'); os.close(fd)
    p=Path(name); p.write_text(text,encoding='utf-8'); py_compile.compile(str(p),doraise=True); return p


def wait_http(url,seconds=60):
    end=time.time()+seconds; last=None
    while time.time()<end:
        try:
            with urllib.request.urlopen(url,timeout=3) as r:
                if r.status==200:return
        except Exception as e:last=e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP {url}: {last}')


def patch_broker(s):
    if 'def deposit_usd(' in s:return s
    anchor='''    def balance(self, symbol: str = "", exchange: str = "NY") -> dict[str, Any]:\n        ex = self._check_exchange(exchange)\n        return self._post("/api/us/acnt", "ust21070", {"stex_tp": ex, "stk_cd": str(symbol).upper().strip()})\n'''
    add=anchor+'''\n    def deposit_usd(self) -> dict[str, Any]:\n        # Kiwoom US mock overseas-stock deposit (USD/orderable cash).\n        return self._post("/api/us/acnt", "ust21110", {})\n'''
    if anchor not in s:raise SystemExit('ABORT broker balance anchor missing')
    return s.replace(anchor,add,1)


def patch_v4(s):
    if 'V45_LIVE_ELIGIBLE_US_FINDER' in s:return s
    old="""            if quality not in ('A','B_EVENT','C_HIGH_RISK'):\n                # V4.6.2 Discovery Bridge Shadow:\n                # production still rejects rows without verified discovery quality.\n                # Shadow may evaluate Screener-eligible misses conservatively with\n                # zero quality bonus. This never mutates live Finder when commit=False.\n                if shadow_allow_unknown_quality and bool(c.get('eligible')):\n                    quality='SHADOW_UNKNOWN'\n                    shadow_quality_unknown=True\n                else:\n                    continue\n"""
    new="""            if quality not in ('A','B_EVENT','C_HIGH_RISK'):\n                # V45_LIVE_ELIGIBLE_US_FINDER: during PREMARKET/REGULAR, a live\n                # Screener-eligible row with real tape data may enter Finder even\n                # when the discovery quality label is missing. V22E remains the\n                # final entry authority; this only prevents an empty upstream Finder.\n                if bool(c.get('eligible')) and _session('USA') in ('PREMARKET','REGULAR'):\n                    quality='LIVE_ELIGIBLE'\n                    shadow_quality_unknown=True\n                elif shadow_allow_unknown_quality and bool(c.get('eligible')):\n                    quality='SHADOW_UNKNOWN'\n                    shadow_quality_unknown=True\n                else:\n                    continue\n"""
    if old not in s:raise SystemExit('ABORT V4 USA quality anchor missing')
    return s.replace(old,new,1)


def patch_api(s):
    # Lift USA Finder production selection from TOP5 to TOP20. Heavy Tracker remains independent.
    s=s.replace('v4.build_usa_finder(usa_candidates,k.discovery,5,db=db)','v4.build_usa_finder(usa_candidates,k.discovery,20,db=db)')
    s=s.replace('v4.build_usa_finder(screener_rows(db.quotes(),db.daily_metrics(),40),k.discovery,5,db=db)','v4.build_usa_finder(screener_rows(db.quotes(),db.daily_metrics(),40),k.discovery,20,db=db)')
    return s


def patch_runner(s):
    if 'V45_REGULAR_OPEN_REEVAL' not in s:
        old="""EVAL_PATH = Path(os.getenv('V22E_US_EVAL_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'))\nLOG_PATH = Path(os.getenv('V22E_US_LOG_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl'))\nV44_PREMARKET_EVAL_ORDER_GATE = True"""
        new="""EVAL_PATH = Path(os.getenv('V22E_US_EVAL_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'))\nACCOUNT_PATH = Path(os.getenv('V22E_US_ACCOUNT_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_account.json'))\nLOG_PATH = Path(os.getenv('V22E_US_LOG_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl'))\nV44_PREMARKET_EVAL_ORDER_GATE = True\nV45_REGULAR_OPEN_REEVAL = True"""
        if old not in s:raise SystemExit('ABORT runner V44 path anchor missing')
        s=s.replace(old,new,1)

        anchor="""def refresh_holdings(force=False):\n"""
        helper="""def publish_live_account():\n    # Read the real Kiwoom US MOCK account, never the internal PaperBroker.\n    # ust21070 supplies holdings; ust21110 supplies USD deposit/orderable cash.\n    b = broker()\n    holdings = {}\n    errors = []\n    for ex in ('NY','ND','NA'):\n        try:\n            r=b.balance('',ex)\n            for x in r.get('result_list') or []:\n                sym=str(x.get('stk_cd') or '').upper().strip()\n                qty=i(x.get('poss_qty') or x.get('sell_alowq') or x.get('qty') or 0)\n                if not sym or qty<=0: continue\n                holdings[sym]={\n                    'symbol':sym,'exchange':ex,'name':x.get('frgn_stk_nm') or sym,\n                    'qty':qty,'sellable_qty':i(x.get('sell_alowq') or qty),\n                    'avg':f(x.get('frgn_stk_book_uv')),'price':f(x.get('now_pric')),\n                    'market_value':f(x.get('evlt_amt')),'pnl':f(x.get('pl_amt')),\n                    'pnl_pct':f(x.get('pl_rt')),'purchase_amount':f(x.get('frgn_stk_book_amt')),\n                }\n        except Exception as e:\n            errors.append(f'{ex}:{e!r}')\n        time.sleep(0.35)\n    deposit=0.0; orderable=0.0\n    try:\n        d=b.deposit_usd()\n        for x in d.get('result_list') or []:\n            if str(x.get('crnc_code') or '').upper()=='USD':\n                deposit=f(x.get('fc_entra'))\n                orderable=f(x.get('fc_pymn_alowa')) or deposit\n                break\n    except Exception as e:\n        errors.append(f'USD:{e!r}')\n    stock_value=sum((h.get('market_value') or (h.get('qty',0)*h.get('price',0))) for h in holdings.values())\n    purchase=sum((h.get('purchase_amount') or (h.get('qty',0)*h.get('avg',0))) for h in holdings.values())\n    pnl=sum(h.get('pnl',0) for h in holdings.values())\n    payload={\n        'broker':'KIWOOM_US_MOCK_ONLY','account_api':'ust21070','cash_api':'ust21110',\n        'currency':'USD','deposit':deposit,'cash':orderable,'stock_value':stock_value,\n        'purchase_amount':purchase,'total_assets':deposit+stock_value,'pnl':pnl,\n        'holdings':list(holdings.values()),'holding_count':len(holdings),\n        'errors':errors,'updated_at':datetime.now(timezone.utc).isoformat(),\n    }\n    try:\n        tmp=ACCOUNT_PATH.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); tmp.replace(ACCOUNT_PATH)\n    except Exception as e:\n        log('ACCOUNT_STATE_WRITE_ERROR',error=repr(e))\n    return payload\n\n\n"""
        if anchor not in s:raise SystemExit('ABORT runner refresh anchor missing')
        s=s.replace(anchor,helper+anchor,1)

        s=s.replace("    state = load_state()\n    eval_store = {}\n    last_bar = {}","    state = load_state()\n    eval_store = {}\n    last_bar = {}\n    was_regular = False\n    last_account_publish = 0.0\n    last_heartbeat = 0.0",1)

        old="""            now_et, et_min, premarket, regular, evaluation_session = session()\n            holdings = refresh_holdings()\n"""
        new="""            now_et, et_min, premarket, regular, evaluation_session = session()\n            if regular and not was_regular:\n                # At 09:30 ET, re-evaluate the latest completed causal 5m bar even\n                # if it was already evaluated during PREMARKET. This activates the\n                # regular-session order gate immediately instead of waiting for 09:35.\n                last_bar.clear()\n                log('REGULAR_OPEN_REEVAL', et=now_et.isoformat(), order_gate='ENABLED')\n            was_regular = regular\n            holdings = refresh_holdings()\n            _mono=time.monotonic()\n            if _mono-last_account_publish>=30:\n                acct=publish_live_account(); last_account_publish=_mono\n            if _mono-last_heartbeat>=30:\n                log('V22E_HEARTBEAT', session='REGULAR' if regular else 'PREMARKET' if premarket else 'CLOSED', holdings=len(holdings), eval_rows=len(eval_store), order_gate='ENABLED' if regular else 'DISABLED')\n                last_heartbeat=_mono\n"""
        if old not in s:raise SystemExit('ABORT runner main session anchor missing')
        s=s.replace(old,new,1)
    return s


def patch_app(s):
    if 'V45_US_ACCOUNT_PATH' not in s:
        anchor="V44_US_EVAL_PATH='/home/ubuntu/day-trader-api/v22e_us_mock_eval.json'"
        if anchor not in s:raise SystemExit('ABORT V5 V44 eval anchor missing')
        s=s.replace(anchor,anchor+"\nV45_US_ACCOUNT_PATH='/home/ubuntu/day-trader-api/v22e_us_mock_account.json'",1)
        helper="""\ndef v45_us_live_account():\n    try:\n        import json\n        from pathlib import Path as _V45Path\n        p=_V45Path(V45_US_ACCOUNT_PATH)\n        d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}\n        return d if isinstance(d,dict) else {}\n    except Exception:\n        return {}\n\n"""
        pos=s.find('def api(path, timeout=10):')
        if pos<0:raise SystemExit('ABORT V5 api anchor missing')
        s=s[:pos]+helper+s[pos:]

    # US summary must come from the real Kiwoom US MOCK account snapshot.
    pat=re.compile(r"else:\n\s+pr,_=position_rows\(\); total=cash=invested=0; holds=len\(\[x for x in pr if str\(x.get\('market'\) or ''\)\.upper\(\) in \('',market\)\]\); upd='-'")
    repl="""else:\n    _ua=v45_us_live_account(); total=f(_ua.get('total_assets')); cash=f(_ua.get('cash')); invested=f(_ua.get('stock_value')); holds=int(_ua.get('holding_count') or len(_ua.get('holdings') or [])); upd=str(_ua.get('updated_at') or '-'); upd=upd[-14:-6] if upd!='-' else '-'"""
    s,n=pat.subn(repl,s,count=1)
    if n==0:
        # tolerate local spacing variants
        s=s.replace("else:\n    pr,_=position_rows(); total=cash=invested=0; holds=len([x for x in pr if str(x.get('market') or '').upper() in ('',market)]); upd='-'",repl,1)

    # When the legacy V4 Finder is temporarily empty, show live V22E evaluations
    # rather than an empty terminal. Normal Finder rows still have first priority.
    old="source=(finders[:20] if finders else rows[:20])"
    new="""source=(finders[:20] if finders else rows[:20])\n    if market=='USA' and not source:\n        _ev=list(v44_us_eval_rows().values())\n        _ev.sort(key=lambda x:f(x.get('effective_score') if x.get('effective_score') is not None else x.get('score')) ,reverse=True)\n        source=[]\n        for _r in _ev[:20]:\n            _x=dict(_r); _x['finder_score']=_x.get('effective_score') if _x.get('effective_score') is not None else _x.get('score'); _x['price']=_x.get('price'); _x['finder_reason']=_x.get('reason') or 'V22E live evaluation'; source.append(_x)"""
    if old in s:s=s.replace(old,new,1)

    # Correct the selected-market engine badge if any old literal survived V42/V44.
    if "market_txt='KR' if market=='KOREA' else 'US'" in s and "engine_txt='ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22E'" not in s:
        s=s.replace("market_txt='KR' if market=='KOREA' else 'US'","market_txt='KR' if market=='KOREA' else 'US'; engine_txt='ENGINE5 V22' if market=='KOREA' else 'ENGINE5 V22E'",1)
    s=s.replace(" &nbsp;&nbsp; ENGINE5 V22 &nbsp;&nbsp; <span class=\"g\">LIVE</span>"," &nbsp;&nbsp; '+engine_txt+' &nbsp;&nbsp; <span class=\"g\">LIVE</span>")
    s=s.replace('DAY TRADER V5 <small>v44</small>','DAY TRADER V5 <small>v45</small>')
    return s


def main():
    for p in (RUNNER,BROKER,V4,API,APP):
        if not p.exists():raise SystemExit('ABORT missing '+str(p))
    old={p:p.read_text(encoding='utf-8') for p in (RUNNER,BROKER,V4,API,APP)}
    new={
        BROKER:patch_broker(old[BROKER]),
        V4:patch_v4(old[V4]),
        API:patch_api(old[API]),
        RUNNER:patch_runner(old[RUNNER]),
        APP:patch_app(old[APP]),
    }
    tmps={}
    try:
        for p,text in new.items():tmps[p]=compile_text(text,'v45_')
        print('PY_COMPILE=PASS',flush=True)
        for p in (BROKER,V4,API,RUNNER):
            bak=p.with_suffix(p.suffix+'.pre_v45')
            if not bak.exists():run('sudo','cp','-a',p,bak)
            run('sudo','install','-m','0644',tmps[p],p)
        APP_BAK=APP.with_suffix('.py.pre_v45')
        if not APP_BAK.exists():APP_BAK.write_text(old[APP],encoding='utf-8')
        APP.write_text(new[APP],encoding='utf-8')
    finally:
        for p in tmps.values():p.unlink(missing_ok=True)

    # Import the actual runtime before restart.
    code="import os,sys;os.chdir('/home/ubuntu/day-trader-api');sys.path.insert(0,'/home/ubuntu/day-trader-api');from dotenv import load_dotenv;load_dotenv('/home/ubuntu/day-trader-api/.env');import live_server.api,live_server.v4_engine,live_server.v22e_us_mock_live;print('RUNTIME_IMPORT=PASS')"
    p=run('sudo','-u','ubuntu','-H',RUNTIME/'venv/bin/python','-c',code,check=False,capture=True)
    if p.stdout.strip():print(p.stdout.strip(),flush=True)
    if p.returncode!=0:
        if p.stderr.strip():print(p.stderr,flush=True)
        raise SystemExit('ABORT runtime import failed')

    run('sudo','systemctl','restart','day-trader-api')
    wait_http('http://127.0.0.1:8000/health',60); print('API_HEALTH=PASS',flush=True)
    run('sudo','systemctl','restart','day-trader-v22e-us')
    time.sleep(5)
    if subprocess.check_output(['sudo','systemctl','is-active','day-trader-v22e-us'],text=True).strip()!='active':
        run('sudo','journalctl','-u','day-trader-v22e-us','-n','100','--no-pager',check=False);raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE',flush=True)

    # Wait for real account snapshot + regular-session evaluation heartbeat.
    acct=RUNTIME/'v22e_us_mock_account.json'; ev=RUNTIME/'v22e_us_mock_eval.json'
    end=time.time()+45
    while time.time()<end and not acct.exists():time.sleep(2)
    if acct.exists():
        d=json.loads(acct.read_text(encoding='utf-8')); print('US_MOCK_ACCOUNT=READY total_assets='+str(d.get('total_assets'))+' cash='+str(d.get('cash'))+' holdings='+str(d.get('holding_count')),flush=True)
    else:print('US_MOCK_ACCOUNT=PENDING',flush=True)

    # USA Finder should refresh quickly in DAYTRADE; give it up to 45 sec.
    fcount=0; session='-'
    end=time.time()+45
    while time.time()<end:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=4) as r:d=json.loads(r.read().decode())
            session=str(d.get('session') or '-'); fcount=len(((d.get('finder') or {}).get('rows') or []))
            if fcount>0:break
        except Exception:pass
        time.sleep(3)
    print(f'USA_SESSION={session}',flush=True); print(f'USA_FINDER_ROWS={fcount}',flush=True)

    # Restart V5 as ubuntu with writable log.
    run('sudo','rm','-f',LOG,check=False);run('sudo','-u','ubuntu','touch',LOG);run('sudo','chown','ubuntu:ubuntu',LOG)
    subprocess.run(['sudo','pkill','-f','streamlit run app_v5.py'],check=False);time.sleep(1)
    cmd=f"cd {REPO} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &"
    run('sudo','-u','ubuntu','-H','bash','-lc',cmd)
    wait_http(f'http://127.0.0.1:{PORT}/',45);print('V5_HTTP=PASS',flush=True)

    print('US_BROKER=KIWOOM_US_MOCK_ONLY',flush=True)
    print('US_ACCOUNT_SOURCE=ust21070+ust21110',flush=True)
    print('US_REGULAR_OPEN_REEVAL=ENABLED',flush=True)
    print('US_FINDER_LIVE_ELIGIBLE_BRIDGE=ENABLED',flush=True)
    print('US_FINDER_LIMIT=20',flush=True)
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA',flush=True)
    print('WILLIAMS_US_ORDER_AUTHORITY=DISABLED_UNCHANGED',flush=True)
    print('DEPLOY=PASS',flush=True)

if __name__=='__main__':main()
