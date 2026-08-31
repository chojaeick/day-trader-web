#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path

RUNTIME = Path('/home/ubuntu/day-trader-api')
REPO = Path('/home/ubuntu/day-trader-api-repo')
ENV = RUNTIME / '.env'
CODE_ROOTS = [RUNTIME / 'live_server', REPO / 'live_server', REPO / 'tools']

KEY_RE = re.compile(r'(USA|US_|AMERICA|OVERSEAS|PAPER|MOCK|BROKER|ALPACA|KIS|KIWOOM|ACCOUNT|ACCT|ORDER|TRADING)', re.I)
CODE_RE = re.compile(r'(usa|overseas|paper|mock|broker|alpaca|kis|kiwoom|buy_order|sell_order|place_order|order_market|market_order|williams_usa)', re.I)
SECRET_RE = re.compile(r'(secret|token|password|passwd|app[_-]?key|app[_-]?secret|access[_-]?key|private)', re.I)
ORDER_EVENT_RE = re.compile(r'(USA|US |AMERICA|PAPER|MOCK|BUY|SELL|ORDER|FILL|TRADE|WILLIAMS_USA|FROZEN_USA)', re.I)


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=20)
    except Exception as e:
        return f'<ERROR {e}>'


def mask_env_value(key: str, value: str) -> str:
    if SECRET_RE.search(key):
        return '***MASKED***'
    if 'ACCOUNT' in key.upper() or 'ACCT' in key.upper():
        v = value.strip()
        if len(v) > 4:
            return '***' + v[-4:]
    if len(value) > 120:
        return value[:117] + '...'
    return value


def env_section() -> None:
    print('===== ENV CANDIDATES (SECRETS MASKED) =====')
    if not ENV.exists():
        print('ENV_NOT_FOUND')
        return
    try:
        text = ENV.read_text(errors='ignore')
    except PermissionError:
        print('ENV_PERMISSION_DENIED: rerun with sudo')
        return
    found = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if KEY_RE.search(key):
            print(f'{key}={mask_env_value(key, value.strip())}')
            found += 1
    if not found:
        print('NO_MATCHING_ENV_KEYS')


def code_section() -> None:
    print('\n===== USA ORDER/BROKER CODE HITS =====')
    hits = []
    seen = set()
    for root in CODE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*.py'):
            try:
                rel = str(path)
                for i, line in enumerate(path.read_text(errors='ignore').splitlines(), 1):
                    if CODE_RE.search(line):
                        key = (rel, i, line.strip())
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(key)
            except Exception:
                continue
    for rel, i, line in hits[:500]:
        # Never echo literal-looking secrets from source lines.
        safe = re.sub(r'([A-Za-z_]*(?:SECRET|TOKEN|PASSWORD|APP_KEY|APP_SECRET)[A-Za-z_]*\s*=\s*)[\'\"][^\'\"]+[\'\"]', r'\1"***MASKED***"', line, flags=re.I)
        print(f'{rel}:{i}: {safe}')
    if not hits:
        print('NO_CODE_HITS')


def journal_section() -> None:
    print('\n===== FRIDAY 2026-08-28 USA TRADE JOURNAL HITS =====')
    # UTC+KST/ET ambiguity is handled by scanning the whole calendar day plus nearby hours.
    out = sh(['journalctl', '-u', 'day-trader-api', '--since', '2026-08-28 00:00:00', '--until', '2026-08-29 12:00:00', '--no-pager'])
    rows = [ln for ln in out.splitlines() if ORDER_EVENT_RE.search(ln)]
    for ln in rows[-500:]:
        print(ln)
    if not rows:
        print('NO_MATCHING_JOURNAL_LINES')


def sqlite_section() -> None:
    print('\n===== SQLITE USA/PAPER/ORDER TABLES =====')
    dbs = []
    for base in (RUNTIME, REPO):
        if not base.exists():
            continue
        for pat in ('*.db', '*.sqlite', '*.sqlite3'):
            dbs.extend(base.rglob(pat))
    uniq = []
    seen = set()
    for p in dbs:
        s = str(p)
        if s not in seen:
            seen.add(s); uniq.append(p)
    for db in uniq[:50]:
        try:
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=1)
            tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
            matched = [t for t in tabs if re.search(r'(usa|paper|trade|order|fill|position|account|williams)', t, re.I)]
            if matched:
                print(f'DB={db}')
                for t in matched:
                    try:
                        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    except Exception:
                        n = '?'
                    print(f'  {t} rows={n}')
            con.close()
        except Exception:
            continue


def modified_section() -> None:
    print('\n===== FILES MODIFIED AROUND FRIDAY SESSION =====')
    for base in (RUNTIME / 'live_server', REPO / 'live_server', REPO / 'tools'):
        if not base.exists():
            continue
        out = sh(['find', str(base), '-type', 'f', '-newermt', '2026-08-28 00:00:00', '!', '-newermt', '2026-08-30 12:00:00', '-printf', '%TY-%Tm-%Td %TH:%TM:%TS %p\n'])
        for ln in out.splitlines()[:300]:
            print(ln)


def main() -> None:
    print('USA_MOCK_FORENSIC_DIAGNOSTIC=START')
    env_section()
    code_section()
    journal_section()
    sqlite_section()
    modified_section()
    print('\nUSA_MOCK_FORENSIC_DIAGNOSTIC=END')
    print('NO_RUNTIME_CHANGES=TRUE')


if __name__ == '__main__':
    main()
