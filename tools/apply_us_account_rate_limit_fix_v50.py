#!/usr/bin/env python3
from __future__ import annotations

import json, os, py_compile, subprocess, tempfile, time, urllib.request
from pathlib import Path

RUNTIME=Path('/home/ubuntu/day-trader-api')
RUNNER=RUNTIME/'live_server'/'v22e_us_mock_live.py'
ACCOUNT=RUNTIME/'v22e_us_mock_account.json'
SERVICE='day-trader-v22e-us'


def run(*args,check=True,capture=False,cwd=None):
    print('+',' '.join(map(str,args)),flush=True)
    return subprocess.run(list(map(str,args)),check=check,text=True,capture_output=capture,cwd=str(cwd) if cwd else None)


def compile_text(text,prefix):
    fd,name=tempfile.mkstemp(prefix=prefix,suffix='.py'); os.close(fd)
    p=Path(name); p.write_text(text,encoding='utf-8'); py_compile.compile(str(p),doraise=True); return p


def patch_runner(s:str)->str:
    if 'V50_ACCOUNT_RATE_LIMIT_SAFE = True' in s:
        return s
    marker='V49_LIVE_EXCHANGE_RESOLUTION = True'
    if marker not in s: raise SystemExit('ABORT V49 marker missing')
    s=s.replace(marker,marker+'\nV50_ACCOUNT_RATE_LIMIT_SAFE = True',1)

    # Replace the aggressive V48 multi-symbol/multi-exchange reconciliation with
    # one whole-account read plus a tiny targeted fallback for persisted holdings.
    rstart=s.find('def refresh_holdings(force=False):')
    rend=s.find('\n\ndef finder_symbols(',rstart)
    if rstart<0 or rend<0: raise SystemExit('ABORT refresh_holdings block missing')
    refresh=r'''def refresh_holdings(force=False):
    global _last_recon, _holdings_cache
    now=time.monotonic()
    if not force and now-_last_recon<RECON_SEC:
        return dict(_holdings_cache)
    out={}; b=broker()
    try:
        r=b.balance_all()
        for x in r.get('result_list') or []:
            sym=str(x.get('stk_cd') or '').upper().strip()
            ex=str(x.get('stex_tp') or resolve_exchange(sym))
            h=parse_holding(x,ex)
            if h: out[h['symbol']]=h
    except Exception as e:
        log('ACCOUNT_READ_ERROR',exchange='ALL',error=repr(e))

    # Mock whole-account ust21070 may return no result_list. Never brute-force
    # Finder candidates. Only re-check positions already persisted by the engine
    # plus SPCX, the known pre-existing mock holding seen in the UI.
    if not out:
        known=[]
        try: known += list(load_state().keys())
        except Exception: pass
        known += ['SPCX']
        seen=[]
        for sym in known:
            sym=str(sym or '').upper().strip()
            if sym and sym not in seen: seen.append(sym)
        for sym in seen[:4]:
            ex=resolve_exchange(sym)
            try:
                rr=b.balance(sym,ex)
                for x in rr.get('result_list') or []:
                    h=parse_holding(x,ex)
                    if h: out[h['symbol']]=h
            except Exception as e:
                log('ACCOUNT_READ_ERROR',exchange=ex,symbol=sym,error=repr(e))
            time.sleep(1.20)
    _holdings_cache=out; _last_recon=time.monotonic(); return dict(out)
'''
    s=s[:rstart]+refresh+s[rend:]

    # Replace account publisher's aggressive targeted scan. It may publish zero,
    # but must not generate dozens of account calls and 429s.
    pstart=s.find('def publish_live_account():')
    pend=s.find('\n\ndef refresh_holdings(',pstart)
    if pstart<0 or pend<0: raise SystemExit('ABORT publish_live_account block missing')
    publish=r'''def publish_live_account():
    b=broker(); errors=[]; holdings={}; bal={}; dep={}
    try:
        bal=b.balance_all()
    except Exception as e:
        errors.append('ust21070_all:'+repr(e))
    for x in bal.get('result_list') or []:
        sym=str(x.get('stk_cd') or '').upper().strip(); qty=i(x.get('poss_qty') or x.get('sell_alowq') or 0)
        if not sym or qty<=0: continue
        ex=str(x.get('stex_tp') or resolve_exchange(sym))
        holdings[sym]={
            'symbol':sym,'exchange':ex,'name':x.get('frgn_stk_nm') or sym,
            'qty':qty,'sellable_qty':i(x.get('sell_alowq') or qty),
            'avg':f(x.get('frgn_stk_book_uv')),'price':f(x.get('now_pric')),
            'market_value':f(x.get('evlt_amt')),'purchase_amount':f(x.get('frgn_stk_book_amt')),
            'pnl':f(x.get('pl_amt')),'pnl_pct':f(x.get('pl_rt')),
        }
    if not holdings:
        known=[]
        try: known += list(load_state().keys())
        except Exception: pass
        known += ['SPCX']
        seen=[]
        for sym in known:
            sym=str(sym or '').upper().strip()
            if sym and sym not in seen: seen.append(sym)
        for sym in seen[:4]:
            ex=resolve_exchange(sym)
            try:
                rr=b.balance(sym,ex)
                for x in rr.get('result_list') or []:
                    qty=i(x.get('poss_qty') or x.get('sell_alowq') or 0)
                    rsym=str(x.get('stk_cd') or '').upper().strip()
                    if not rsym or qty<=0: continue
                    holdings[rsym]={
                        'symbol':rsym,'exchange':ex,'name':x.get('frgn_stk_nm') or rsym,
                        'qty':qty,'sellable_qty':i(x.get('sell_alowq') or qty),
                        'avg':f(x.get('frgn_stk_book_uv')),'price':f(x.get('now_pric')),
                        'market_value':f(x.get('evlt_amt')),'purchase_amount':f(x.get('frgn_stk_book_amt')),
                        'pnl':f(x.get('pl_amt')),'pnl_pct':f(x.get('pl_rt')),
                    }
            except Exception as e:
                errors.append(f'ust21070_{sym}_{ex}:'+repr(e))
            time.sleep(1.20)
    try:
        dep=b.deposit_usd()
    except Exception as e:
        errors.append('ust21110:'+repr(e))
    def nums(obj,key):
        out=[]
        if isinstance(obj,dict):
            for k,v in obj.items():
                if k==key:
                    try: out.append(float(str(v).replace(',','').strip() or 0))
                    except Exception: pass
                if isinstance(v,(dict,list)): out.extend(nums(v,key))
        elif isinstance(obj,list):
            for v in obj: out.extend(nums(v,key))
        return out
    cashs=nums(dep,'fc_entra'); cash=max(cashs) if cashs else 0.0
    stock_value=f(bal.get('tot_evlt_amt')) or sum((h.get('market_value') or h.get('qty',0)*h.get('price',0)) for h in holdings.values())
    purchase=f(bal.get('tot_prch_amt')) or sum((h.get('purchase_amount') or h.get('qty',0)*h.get('avg',0)) for h in holdings.values())
    pnl=f(bal.get('tot_pl_amt')) or sum(h.get('pnl',0) for h in holdings.values())
    status='READY' if (holdings or cash or stock_value) else 'EMPTY_OR_CREDENTIAL_MISMATCH'
    payload={
        'broker':'KIWOOM_US_MOCK_ONLY','account_api':'ust21070','cash_api':'ust21110',
        'account_status':status,'currency':'USD','cash':cash,'stock_value':stock_value,
        'purchase_amount':purchase,'total_assets':cash+stock_value,'pnl':pnl,
        'holdings':list(holdings.values()),'holding_count':len(holdings),
        'deposit_return_msg':dep.get('return_msg') if isinstance(dep,dict) else None,
        'errors':errors[-6:],'updated_at':datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp=ACCOUNT_PATH.with_suffix('.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); tmp.replace(ACCOUNT_PATH)
    except Exception as e: log('ACCOUNT_STATE_WRITE_ERROR',error=repr(e))
    return payload
'''
    s=s[:pstart]+publish+s[pend:]
    return s


def stopped_probe():
    # Service is stopped so this read-only probe has no concurrent ust21070 traffic.
    code=r'''import os,sys,json,time
os.chdir('/home/ubuntu/day-trader-api');sys.path.insert(0,'/home/ubuntu/day-trader-api')
from dotenv import load_dotenv;load_dotenv('/home/ubuntu/day-trader-api/.env')
from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker
pref=bool(os.getenv('KIWOOM_US_MOCK_APP_KEY')) and bool(os.getenv('KIWOOM_US_MOCK_APP_SECRET'))
legacy=bool(os.getenv('KIWOOM_MOCK_APP_KEY')) and bool(os.getenv('KIWOOM_MOCK_APP_SECRET'))
print('US_CRED_SOURCE', 'KIWOOM_US_MOCK' if pref else 'KIWOOM_MOCK_LEGACY' if legacy else 'MISSING')
print('US_CRED_PREFERRED_PRESENT',pref)
print('US_CRED_LEGACY_PRESENT',legacy)
if pref and legacy:
 print('US_CRED_SETS_DIFFER', (os.getenv('KIWOOM_US_MOCK_APP_KEY')!=os.getenv('KIWOOM_MOCK_APP_KEY')) or (os.getenv('KIWOOM_US_MOCK_APP_SECRET')!=os.getenv('KIWOOM_MOCK_APP_SECRET')))
b=KiwoomUSMockBroker()
try:
 r=b.balance('SPCX','ND'); print('SPCX_ND',json.dumps({'return_code':r.get('return_code'),'return_msg':r.get('return_msg'),'rows':len(r.get('result_list') or []),'result_list':r.get('result_list') or [],'tot_evlt_amt':r.get('tot_evlt_amt'),'tot_prch_amt':r.get('tot_prch_amt')},ensure_ascii=False)[:7000])
except Exception as e: print('SPCX_ND_ERROR',repr(e))
time.sleep(1.3)
try:
 d=b.deposit_usd(); print('UST21110',json.dumps({k:v for k,v in d.items() if k not in ('token','authorization')},ensure_ascii=False)[:7000])
except Exception as e: print('UST21110_ERROR',repr(e))
'''
    p=run('sudo','-u','ubuntu','-H',RUNTIME/'venv/bin/python','-c',code,check=False,capture=True,cwd=RUNTIME)
    print('QUIET_ACCOUNT_PROBE_BEGIN')
    if p.stdout: print(p.stdout.strip())
    if p.stderr: print('PROBE_STDERR',p.stderr.strip())
    print('QUIET_ACCOUNT_PROBE_END')
    if p.returncode!=0: raise SystemExit('ABORT quiet account probe failed')


def main():
    if not RUNNER.exists(): raise SystemExit('ABORT runner missing')
    old=RUNNER.read_text(encoding='utf-8'); new=patch_runner(old); tmp=compile_text(new,'v50_')
    print('PY_COMPILE=PASS')
    try:
        bak=Path(str(RUNNER)+'.pre_v50')
        if not bak.exists(): run('sudo','cp','-a',RUNNER,bak)
        run('sudo','install','-m','0644',tmp,RUNNER)
    finally: tmp.unlink(missing_ok=True)

    run('sudo','systemctl','stop',SERVICE)
    time.sleep(2)
    stopped_probe()
    run('sudo','systemctl','start',SERVICE)
    time.sleep(6)
    if subprocess.check_output(['sudo','systemctl','is-active',SERVICE],text=True).strip()!='active':
        run('sudo','journalctl','-u',SERVICE,'-n','100','--no-pager',check=False); raise SystemExit('ABORT V22E inactive')
    print('V22E_SERVICE=ACTIVE')
    try: d=json.loads(ACCOUNT.read_text(encoding='utf-8')) if ACCOUNT.exists() else {}
    except Exception: d={}
    print('US_ACCOUNT_STATUS='+str(d.get('account_status') or 'PENDING'))
    print('US_ACCOUNT_RATE_LIMIT_POLICY=WHOLE_ONCE_PLUS_MAX4_PERSISTED')
    print('US_EXCHANGE_SOURCE=LIVE_DB_QUOTE_THEN_STATIC_FALLBACK')
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA')
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA')
    print('DEPLOY=PASS')

if __name__=='__main__': main()
