#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
REPO=Path('/home/ubuntu/day-trader-api-repo')
API=RUNTIME/'live_server'/'api.py'
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
CONFIG=RUNTIME/'live_server'/'config.py'
ACCOUNT=RUNTIME/'v22e_us_mock_account.json'
EVAL=RUNTIME/'v22e_us_mock_eval.json'
APP=REPO/'app_v5.py'
LOG=Path('/tmp/daytrader-v5.log')
PORT=8503


def run(*args,check=True,capture=False,cwd=None):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check,text=True,capture_output=capture,cwd=str(cwd) if cwd else None)


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


def patch_api(s):
    # V47 finder helper uses json; api.py historically did not import it.
    if 'import json\n' not in s[:1200]:
        anchor='import sqlite3\n'
        if anchor not in s: raise SystemExit('ABORT api import anchor missing')
        s=s.replace(anchor,anchor+'import json\n',1)
    return s


def patch_config(s):
    # NU (Nu Holdings) is NYSE. Defaulting unknown US symbols to ND caused a real 1903 reject.
    if "'NU':'NY'" not in s:
        anchor="'DELL':'NY','HOOD':'ND','RKLB':'ND','TSM':'NY'"
        if anchor not in s: raise SystemExit('ABORT config exchange anchor missing')
        s=s.replace(anchor,"'DELL':'NY','HOOD':'ND','RKLB':'ND','TSM':'NY','NU':'NY'",1)
    return s


def patch_runner(s):
    if 'V48_TARGETED_BALANCE_FALLBACK = True' in s:
        return s
    marker='V47_US_MARKET_OPEN_STABLE = True'
    if marker not in s: raise SystemExit('ABORT V47 marker missing')
    s=s.replace(marker,marker+'\nV48_TARGETED_BALANCE_FALLBACK = True',1)

    start=s.find('def publish_live_account():')
    end=s.find('\n\ndef refresh_holdings(',start)
    if start<0 or end<0: raise SystemExit('ABORT account publisher missing')
    fn=r'''def _recursive_numbers(obj, key):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k==key:
                try: out.append(float(str(v).replace(',','').strip() or 0))
                except Exception: pass
            if isinstance(v,(dict,list)): out.extend(_recursive_numbers(v,key))
    elif isinstance(obj,list):
        for v in obj: out.extend(_recursive_numbers(v,key))
    return out


def _target_symbols():
    out=['SPCX']
    try:
        d=json.loads(EVAL_PATH.read_text(encoding='utf-8')) if EVAL_PATH.exists() else {}
        out += list((d.get('rows') or {}).keys())
    except Exception: pass
    try: out += list(load_state().keys())
    except Exception: pass
    out += list(settings.core_symbols)
    seen=[]
    for x in out:
        x=str(x or '').upper().strip()
        if x and x not in seen: seen.append(x)
    return seen[:24]


def publish_live_account():
    b=broker(); errors=[]; holdings={}; bal={}; dep={}; total_candidates=[]; purchase_candidates=[]; pnl_candidates=[]
    try:
        bal=b.balance_all()
    except Exception as e:
        errors.append('ust21070_all:'+repr(e)); bal={}

    def ingest(resp, ex_hint=''):
        if not isinstance(resp,dict): return 0
        for k,collector in (('tot_evlt_amt',total_candidates),('tot_prch_amt',purchase_candidates),('tot_pl_amt',pnl_candidates)):
            try:
                val=float(str(resp.get(k) or 0).replace(',',''))
                if val: collector.append(val)
            except Exception: pass
        n=0
        for x in resp.get('result_list') or []:
            sym=str(x.get('stk_cd') or '').upper().strip()
            qty=i(x.get('poss_qty') or x.get('sell_alowq') or 0)
            if not sym or qty<=0: continue
            holdings[sym]={
                'symbol':sym,'exchange':ex_hint or str(x.get('stex_tp') or settings.exchange_for(sym)),
                'name':x.get('frgn_stk_nm') or sym,'qty':qty,
                'sellable_qty':i(x.get('sell_alowq') or qty),
                'avg':f(x.get('frgn_stk_book_uv')),'price':f(x.get('now_pric')),
                'market_value':f(x.get('evlt_amt')),'purchase_amount':f(x.get('frgn_stk_book_amt')),
                'pnl':f(x.get('pl_amt')),'pnl_pct':f(x.get('pl_rt')),
            }; n+=1
        return n

    ingest(bal)

    # Kiwoom mock currently returns an empty whole-account result on this credential.
    # Fall back to the exact Friday-safe path: ust21070(symbol, exchange), read-only.
    if not holdings:
        for sym in _target_symbols():
            preferred=settings.exchange_for(sym)
            exchanges=[preferred]+[x for x in ('NY','ND','NA') if x!=preferred]
            found=False
            for ex in exchanges:
                try:
                    r=b.balance(sym,ex)
                    if ingest(r,ex)>0:
                        found=True; break
                except Exception as e:
                    txt=repr(e)
                    if '429' not in txt: errors.append(f'ust21070_{sym}_{ex}:'+txt)
                time.sleep(1.05)
            if found and sym=='SPCX':
                pass

    try:
        dep=b.deposit_usd()
    except Exception as e:
        errors.append('ust21110:'+repr(e)); dep={}

    cash_vals=_recursive_numbers(dep,'fc_entra')
    order_vals=_recursive_numbers(dep,'fc_pymn_alowa')
    cash=max(cash_vals) if cash_vals else 0.0
    orderable=max(order_vals) if order_vals else cash
    row_value=sum((h.get('market_value') or h.get('qty',0)*h.get('price',0)) for h in holdings.values())
    row_purchase=sum((h.get('purchase_amount') or h.get('qty',0)*h.get('avg',0)) for h in holdings.values())
    stock_value=max(total_candidates) if total_candidates else row_value
    purchase=max(purchase_candidates) if purchase_candidates else row_purchase
    pnl=(max(pnl_candidates,key=abs) if pnl_candidates else sum(h.get('pnl',0) for h in holdings.values()))
    payload={
        'broker':'KIWOOM_US_MOCK_ONLY','account_api':'ust21070','cash_api':'ust21110',
        'account_mode':'WHOLE_THEN_TARGETED_FALLBACK','currency':'USD',
        'cash':cash,'orderable_cash':orderable,'stock_value':stock_value,
        'purchase_amount':purchase,'total_assets':cash+stock_value,'pnl':pnl,
        'holdings':list(holdings.values()),'holding_count':len(holdings),
        'deposit_keys':sorted(dep.keys()) if isinstance(dep,dict) else [],
        'errors':errors[-10:],'updated_at':datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp=ACCOUNT_PATH.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); tmp.replace(ACCOUNT_PATH)
    except Exception as e: log('ACCOUNT_STATE_WRITE_ERROR',error=repr(e))
    return payload
'''
    s=s[:start]+fn+s[end:]

    # Trading reconciliation gets the same targeted fallback so broker state is source of truth.
    rstart=s.find('def refresh_holdings(force=False):')
    rend=s.find('\n\ndef finder_symbols(',rstart)
    if rstart<0 or rend<0: raise SystemExit('ABORT refresh_holdings block missing')
    refresh=r'''def refresh_holdings(force=False):
    global _last_recon, _holdings_cache
    now=time.monotonic()
    if not force and now-_last_recon<RECON_SEC: return dict(_holdings_cache)
    out={}; b=broker()
    try:
        r=b.balance_all()
        for x in r.get('result_list') or []:
            sym=str(x.get('stk_cd') or '').upper().strip(); ex=str(x.get('stex_tp') or settings.exchange_for(sym))
            h=parse_holding(x,ex)
            if h: out[h['symbol']]=h
    except Exception as e: log('ACCOUNT_READ_ERROR',exchange='ALL',error=repr(e))
    if not out:
        for sym in _target_symbols()[:12]:
            preferred=settings.exchange_for(sym); exchanges=[preferred]+[x for x in ('NY','ND','NA') if x!=preferred]
            for ex in exchanges:
                try:
                    r=b.balance(sym,ex); got=False
                    for x in r.get('result_list') or []:
                        h=parse_holding(x,ex)
                        if h: out[h['symbol']]=h; got=True
                    if got: break
                except Exception as e:
                    if '429' not in repr(e): log('ACCOUNT_READ_ERROR',exchange=ex,symbol=sym,error=repr(e))
                time.sleep(1.05)
    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)
'''
    s=s[:rstart]+refresh+s[rend:]
    return s


def main():
    for p in (API,RUNNER,CONFIG,APP):
        if not p.exists(): raise SystemExit('ABORT missing '+str(p))
    old={p:p.read_text(encoding='utf-8') for p in (API,RUNNER,CONFIG,APP)}
    new={API:patch_api(old[API]),RUNNER:patch_runner(old[RUNNER]),CONFIG:patch_config(old[CONFIG]),APP:old[APP]}
    tmps={}
    try:
        for p,t in new.items(): tmps[p]=compile_text(t,'v48_')
        print('PY_COMPILE=PASS',flush=True)
        for p,tmp in tmps.items():
            bak=Path(str(p)+'.pre_v48')
            if not bak.exists(): run('sudo','cp','-a',p,bak)
            if str(p).startswith(str(RUNTIME)): run('sudo','install','-m','0644',tmp,p)
    finally:
        for t in tmps.values(): t.unlink(missing_ok=True)

    run('sudo','systemctl','restart','day-trader-api'); wait_http('http://127.0.0.1:8000/health',60); print('API_HEALTH=PASS')
    run('sudo','systemctl','restart','day-trader-v22e-us'); time.sleep(8)
    if subprocess.check_output(['sudo','systemctl','is-active','day-trader-v22e-us'],text=True).strip()!='active':
        run('sudo','journalctl','-u','day-trader-v22e-us','-n','100','--no-pager',check=False); raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE')

    # Give targeted account fallback time to finish.
    end=time.time()+50
    while time.time()<end:
        try:
            d=json.loads(ACCOUNT.read_text(encoding='utf-8')) if ACCOUNT.exists() else {}
            if d.get('account_mode')=='WHOLE_THEN_TARGETED_FALLBACK' and d.get('updated_at'): break
        except Exception: pass
        time.sleep(2)
    d=json.loads(ACCOUNT.read_text(encoding='utf-8')) if ACCOUNT.exists() else {}
    print('US_MOCK_ACCOUNT='+json.dumps({'total_assets':d.get('total_assets'),'cash':d.get('cash'),'orderable_cash':d.get('orderable_cash'),'stock_value':d.get('stock_value'),'holdings':d.get('holding_count'),'symbols':[x.get('symbol') for x in d.get('holdings') or []],'deposit_keys':d.get('deposit_keys'),'errors':d.get('errors')},ensure_ascii=False))

    st=json.loads(urllib.request.urlopen('http://127.0.0.1:8000/api/v4/USA/status',timeout=10).read().decode())
    fr=(st.get('finder') or {}).get('rows') or []
    print('USA_SESSION='+str(st.get('session')))
    print('USA_FINDER_ROWS='+str(len(fr)))
    print('USA_FINDER_SOURCE='+str((st.get('finder') or {}).get('source') or 'V4_LIVE'))
    print('USA_FINDER_SYMBOLS='+','.join(str(x.get('symbol') or '') for x in fr[:20]))

    # V5 restart only; app already reads the V45/V47 live account snapshot.
    run('sudo','rm','-f',LOG,check=False); run('sudo','-u','ubuntu','touch',LOG); run('sudo','chown','ubuntu:ubuntu',LOG)
    subprocess.run(['sudo','pkill','-f','streamlit run app_v5.py'],check=False); time.sleep(1)
    cmd=f"cd {REPO} && DAYTRADER_API_URL=http://127.0.0.1:8000 nohup {RUNTIME}/venv/bin/python -m streamlit run app_v5.py --server.address=0.0.0.0 --server.port={PORT} --server.headless=true > {LOG} 2>&1 &"
    run('sudo','-u','ubuntu','-H','bash','-lc',cmd); wait_http(f'http://127.0.0.1:{PORT}/',45); print('V5_HTTP=PASS')

    j=run('sudo','journalctl','-u','day-trader-v22e-us','-n','60','--no-pager',check=False,capture=True)
    print('V22E_RECENT_BEGIN')
    for line in (j.stdout or '').splitlines():
        if any(x in line for x in ('V22E_HEARTBEAT','ORDER_ATTEMPT','ORDER_ACCEPTED','ORDER_FAILED_NO_RETRY','REGULAR_OPEN_REEVAL')):
            print(line)
    print('V22E_RECENT_END')
    print('US_FINDER_JSON_IMPORT=FIXED')
    print('US_ACCOUNT_TARGETED_FALLBACK=ENABLED')
    print('NU_EXCHANGE=NY')
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA')
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA')
    print('DEPLOY=PASS')

if __name__=='__main__': main()
