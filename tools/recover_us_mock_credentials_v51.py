#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import requests

RUNTIME = Path('/home/ubuntu/day-trader-api')
ENV = RUNTIME / '.env'
SERVICE = 'day-trader-v22e-us'
BASE = 'https://mockapi.kiwoom.com'


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:12] if v else '-'


def parse_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            s = raw.strip()
            if not s or s.startswith('#') or '=' not in s:
                continue
            k, v = s.split('=', 1)
            k = k.strip(); v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            out[k] = v
    except Exception:
        pass
    return out


def candidate_files() -> List[Path]:
    roots = [RUNTIME, Path('/home/ubuntu/day-trader-api-repo'), Path('/home/ubuntu')]
    pats = ['.env', '.env.*', '*.env', '*.env.*', '*env*backup*', '*env*b  ak*', '*env*pre*']
    found: List[Path] = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for pat in pats:
            try:
                for p in root.glob(pat):
                    if p.is_file():
                        rp = str(p.resolve())
                        if rp not in seen and p.stat().st_size <= 2_000_000:
                            seen.add(rp); found.append(p)
            except Exception:
                pass
        # Shallow recursive scan for obvious env backups only.
        try:
            for p in root.glob('**/.env*'):
                if p.is_file() and len(p.parts) <= len(root.parts) + 4:
                    rp = str(p.resolve())
                    if rp not in seen and p.stat().st_size <= 2_000_000:
                        seen.add(rp); found.append(p)
        except Exception:
            pass
    return sorted(found, key=lambda p: str(p))


def token(key: str, secret: str) -> str:
    r = requests.post(BASE + '/oauth2/token', json={
        'grant_type': 'client_credentials', 'appkey': key, 'secretkey': secret
    }, headers={'Content-Type': 'application/json;charset=UTF-8'}, timeout=15)
    r.raise_for_status(); d = r.json()
    if d.get('return_code') not in (None, 0) or not d.get('token'):
        raise RuntimeError('TOKEN_REJECTED')
    return str(d['token'])


def post(tok: str, api_id: str, body: dict) -> dict:
    r = requests.post(BASE + '/api/us/acnt', headers={
        'Content-Type': 'application/json;charset=UTF-8',
        'authorization': 'Bearer ' + tok,
        'api-id': api_id,
    }, json=body, timeout=15)
    r.raise_for_status(); d = r.json()
    if d.get('return_code') not in (None, 0):
        raise RuntimeError(f"{api_id}:{d.get('return_code')}:{d.get('return_msg')}")
    return d


def nonempty_account(key: str, secret: str) -> Tuple[bool, dict]:
    tok = token(key, secret)
    bal = post(tok, 'ust21070', {'stex_tp': 'ND', 'stk_cd': 'SPCX'})
    time.sleep(1.25)
    dep = post(tok, 'ust21110', {})
    rows = bal.get('result_list') or []
    total = float(str(bal.get('tot_evlt_amt') or '0').replace(',', ''))
    # Deposit response varies; treat any non-empty result_list or nonzero top-level numeric as evidence.
    dep_rows = dep.get('result_list') or []
    dep_nonzero = False
    for k, v in dep.items():
        if k in ('return_code', 'return_msg'):
            continue
        try:
            if float(str(v).replace(',', '')) != 0:
                dep_nonzero = True; break
        except Exception:
            pass
    ok = bool(rows or total != 0 or dep_rows or dep_nonzero)
    meta = {
        'spcx_rows': len(rows),
        'spcx_qty': [x.get('poss_qty') or x.get('sell_alowq') for x in rows[:3]],
        'tot_evlt_amt': bal.get('tot_evlt_amt'),
        'deposit_rows': len(dep_rows),
        'deposit_msg': dep.get('return_msg'),
    }
    return ok, meta


def write_preferred(key: str, secret: str) -> None:
    text = ENV.read_text(encoding='utf-8') if ENV.exists() else ''
    def upsert(src: str, name: str, value: str) -> str:
        line = f'{name}={value}'
        pat = re.compile(rf'(?m)^{re.escape(name)}=.*$')
        if pat.search(src):
            return pat.sub(line, src, count=1)
        if src and not src.endswith('\n'):
            src += '\n'
        return src + line + '\n'
    text = upsert(text, 'KIWOOM_US_MOCK_APP_KEY', key)
    text = upsert(text, 'KIWOOM_US_MOCK_APP_SECRET', secret)
    tmp = Path('/tmp/daytrader_env_v51')
    tmp.write_text(text, encoding='utf-8')
    subprocess.run(['sudo', 'install', '-o', 'ubuntu', '-g', 'ubuntu', '-m', '0600', str(tmp), str(ENV)], check=True)
    tmp.unlink(missing_ok=True)


def main() -> None:
    if not ENV.exists():
        raise SystemExit('ABORT .env missing')

    print('V51_SCAN_BEGIN', flush=True)
    files = candidate_files()
    print('ENV_FILES_SCANNED=' + str(len(files)), flush=True)

    candidates: List[Tuple[str, str, str, str]] = []
    seen_pairs = set()
    for p in files:
        d = parse_env(p)
        pairs = []
        if d.get('KIWOOM_US_MOCK_APP_KEY') and d.get('KIWOOM_US_MOCK_APP_SECRET'):
            pairs.append(('PREFERRED', d['KIWOOM_US_MOCK_APP_KEY'], d['KIWOOM_US_MOCK_APP_SECRET']))
        if d.get('KIWOOM_MOCK_APP_KEY') and d.get('KIWOOM_MOCK_APP_SECRET'):
            pairs.append(('LEGACY', d['KIWOOM_MOCK_APP_KEY'], d['KIWOOM_MOCK_APP_SECRET']))
        for kind, key, secret in pairs:
            ident = (key, secret)
            if ident in seen_pairs:
                continue
            seen_pairs.add(ident)
            candidates.append((str(p), kind, key, secret))
            print(f'CANDIDATE source={p} kind={kind} key_fp={fp(key)} secret_fp={fp(secret)}', flush=True)

    print('CANDIDATES=' + str(len(candidates)), flush=True)
    if not candidates:
        raise SystemExit('ABORT NO_CREDENTIAL_CANDIDATES_FOUND')

    # Stop trading runner so read-only probes cannot compete with it for Kiwoom rate limits.
    subprocess.run(['sudo', 'systemctl', 'stop', SERVICE], check=True)
    winner = None
    try:
        for idx, (src, kind, key, secret) in enumerate(candidates, 1):
            try:
                ok, meta = nonempty_account(key, secret)
                print(f'PROBE {idx}/{len(candidates)} key_fp={fp(key)} kind={kind} ok={ok} meta={meta}', flush=True)
                if ok:
                    winner = (src, kind, key, secret, meta)
                    break
            except Exception as e:
                print(f'PROBE {idx}/{len(candidates)} key_fp={fp(key)} kind={kind} ERROR={type(e).__name__}:{e}', flush=True)
            time.sleep(1.5)

        if not winner:
            print('V51_SCAN_END', flush=True)
            raise SystemExit('ABORT NO_NONEMPTY_US_MOCK_ACCOUNT_FOUND')

        src, kind, key, secret, meta = winner
        print(f'WINNER source={src} kind={kind} key_fp={fp(key)} meta={meta}', flush=True)
        write_preferred(key, secret)
        print('ENV_PREFERRED_US_CREDENTIALS=UPDATED', flush=True)
    finally:
        subprocess.run(['sudo', 'systemctl', 'start', SERVICE], check=False)

    time.sleep(5)
    active = subprocess.check_output(['sudo', 'systemctl', 'is-active', SERVICE], text=True).strip()
    print('V22E_SERVICE=' + active.upper(), flush=True)
    if active != 'active':
        raise SystemExit('ABORT V22E_SERVICE_NOT_ACTIVE')
    print('US_CRED_SOURCE=KIWOOM_US_MOCK_PREFERRED', flush=True)
    print('V51_SCAN_END', flush=True)
    print('DEPLOY=PASS', flush=True)


if __name__ == '__main__':
    main()
