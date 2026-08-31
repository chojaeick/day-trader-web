#!/usr/bin/env python3
from __future__ import annotations

"""Guarded Korea mock startup.

Workflow:
1) verify the checked-out v22 source tree is present
2) sync current live_server Python sources into runtime
3) compile-check runtime
4) load runtime .env
5) verify Kiwoom MOCK-only broker configuration/account identity
6) scan mock account balance + current holdings with kt00004
7) only if every preflight check passes, restart day-trader-api
8) verify systemd active + /health response

This script never places a buy/sell order. It only allows the already-configured
mock trading service to start after account state has been observed successfully.
"""

import json
import os
import py_compile
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO = Path('/home/ubuntu/day-trader-api-repo')
RUNTIME = Path('/home/ubuntu/day-trader-api')
ENV = RUNTIME / '.env'
SERVICE = 'day-trader-api'


def fail(msg: str, code: int = 2):
    print(f'KOREA_MOCK_START_ABORT: {msg}', flush=True)
    raise SystemExit(code)


def run(cmd, *, check=True):
    print('+', ' '.join(str(x) for x in cmd), flush=True)
    return subprocess.run(cmd, check=check, text=True, capture_output=False)


def safe_num(v):
    if v is None:
        return None
    s = str(v).replace(',', '').strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def first(d: dict, *keys):
    for k in keys:
        if k in d and d.get(k) not in (None, ''):
            return d.get(k)
    return None


def main():
    if not REPO.exists():
        fail(f'missing repo checkout: {REPO}')
    if not RUNTIME.exists():
        fail(f'missing runtime: {RUNTIME}')
    if not ENV.exists():
        fail(f'missing runtime env: {ENV}')

    # Refuse to deploy a stale/wrong branch checkout.
    branch = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', '--abbrev-ref', 'HEAD'], text=True).strip()
    head = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    origin = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'origin/v22'], text=True).strip()
    print('SOURCE_BRANCH=', branch, flush=True)
    print('SOURCE_HEAD=', head, flush=True)
    print('ORIGIN_V22=', origin, flush=True)
    if branch != 'v22':
        fail(f'repo branch is {branch!r}, expected v22')
    if head != origin:
        fail('local v22 is not equal to origin/v22; run git pull origin v22 first')

    # Sync only Python/source files used by the running API. Runtime data/.env/venv are untouched.
    src = REPO / 'live_server'
    dst = RUNTIME / 'live_server'
    if not src.exists() or not dst.exists():
        fail('live_server source/runtime directory missing')
    copied = 0
    for p in src.rglob('*'):
        if not p.is_file():
            continue
        if p.suffix not in ('.py', '.service'):
            continue
        rel = p.relative_to(src)
        q = dst / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        copied += 1
    print('SYNCED_LIVE_SERVER_FILES=', copied, flush=True)

    # Compile all runtime python sources before touching the service.
    for p in dst.rglob('*.py'):
        py_compile.compile(str(p), doraise=True)
    print('COMPILE_CHECK=PASS', flush=True)

    load_dotenv(ENV, override=True)
    sys.path.insert(0, str(RUNTIME))
    from live_server.kiwoom_mock_broker import KiwoomMockBroker

    broker = KiwoomMockBroker()
    if 'mockapi.kiwoom.com' not in broker.cfg.rest_base:
        fail(f'non-mock REST base refused: {broker.cfg.rest_base}')

    acct = broker.validate_account()
    print('MOCK_REST_BASE=', broker.cfg.rest_base, flush=True)
    print('MOCK_ACCOUNT=', acct, flush=True)
    print('MOCK_ORDER_ENABLE=', broker.cfg.order_enable, flush=True)
    if not broker.cfg.order_enable:
        fail('KIWOOM_MOCK_ORDER_ENABLE is not enabled')

    # kt00004 is the same direct MOCK account snapshot already used by the hard-stop watchdog.
    snap = broker.request_account('kt00004', {'qry_tp': '0', 'dmst_stex_tp': 'KRX'})
    holdings = snap.get('stk_acnt_evlt_prst') or []

    summary = {
        'cash_or_deposit': first(snap, 'entr', 'dnca_tot_amt', 'ord_psbl_cash', 'd2_entra'),
        'total_purchase': first(snap, 'tot_pur_amt', 'tot_buy_amt'),
        'total_eval': first(snap, 'tot_evlt_amt', 'tot_evlt_pl'),
        'estimated_assets': first(snap, 'prsm_dpst_aset_amt', 'tot_asst_amt', 'evlt_asst_tot_amt'),
        'total_pnl': first(snap, 'tot_evlt_pl', 'tot_pl_amt'),
    }
    print('=== MOCK ACCOUNT SNAPSHOT ===', flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    live = []
    for x in holdings:
        sym = str(x.get('stk_cd') or '').replace('A', '').zfill(6)
        qty = int(safe_num(x.get('rmnd_qty')) or 0)
        if qty <= 0:
            continue
        row = {
            'symbol': sym,
            'name': first(x, 'stk_nm', 'stk_name'),
            'qty': qty,
            'avg_price': safe_num(x.get('avg_prc')),
            'current_price': abs(safe_num(x.get('cur_prc')) or 0.0),
            'eval_amount': safe_num(first(x, 'evlt_amt', 'evlt_prft')), 
            'pnl_amount': safe_num(first(x, 'evltv_prft', 'pl_amt')),
            'pnl_pct': safe_num(first(x, 'prft_rt', 'pl_rt')),
        }
        live.append(row)

    print(f'=== MOCK HOLDINGS ({len(live)}) ===', flush=True)
    if live:
        for r in live:
            print(json.dumps(r, ensure_ascii=False), flush=True)
    else:
        print('NO_OPEN_HOLDINGS', flush=True)

    print('ACCOUNT_SCAN=PASS', flush=True)
    print('START_GATE=OPEN', flush=True)

    # Only now restart the API/trading service.
    run(['sudo', 'systemctl', 'restart', SERVICE])
    time.sleep(3)
    active = subprocess.check_output(['systemctl', 'is-active', SERVICE], text=True).strip()
    print('SERVICE_STATE=', active, flush=True)
    if active != 'active':
        fail(f'service failed to become active: {active}')

    try:
        h = requests.get('http://127.0.0.1:8000/health', timeout=10)
        print('HEALTH_HTTP=', h.status_code, flush=True)
        print('HEALTH=', h.text[:1000], flush=True)
        h.raise_for_status()
    except Exception as e:
        fail(f'health check failed after restart: {e}')

    print('KOREA_MOCK_START_OK', flush=True)
    print('ORDER_MODE=KIWOOM_MOCK_ONLY', flush=True)
    print('PREFLIGHT_ORDER=LATEST_CODE -> ACCOUNT_ID -> BALANCE/HOLDINGS -> SERVICE_START', flush=True)


if __name__ == '__main__':
    main()
