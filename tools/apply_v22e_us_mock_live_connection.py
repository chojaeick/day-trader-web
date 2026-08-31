#!/usr/bin/env python3
from __future__ import annotations

"""Deploy V22E as the sole U.S. Kiwoom MOCK order authority.

Safety contract:
- Existing KiwoomUSMockBroker only (mockapi.kiwoom.com).
- ENGINE5_V22E_USA owns both BUY and SELL.
- Williams U.S. mock auto and the legacy DBB-pair auto runner are disabled.
- No internal PaperBroker / SQLite paper account is used for execution.
- One broker order attempt per engine bar/reason; no automatic order retry.
"""

import os
import py_compile
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path('/home/ubuntu/day-trader-api-repo')
RUNTIME = Path('/home/ubuntu/day-trader-api')
ENV = RUNTIME / '.env'
RUNNER_DST = RUNTIME / 'live_server' / 'v22e_us_mock_live.py'
BROKER_SRC = REPO / 'live_server' / 'kiwoom_us_mock_broker.py'
BROKER_DST = RUNTIME / 'live_server' / 'kiwoom_us_mock_broker.py'
SERVICE = Path('/etc/systemd/system/day-trader-v22e-us.service')
API_SERVICE = 'day-trader-api'
V22E_SERVICE = 'day-trader-v22e-us'

RUNNER = r'''from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from live_server.analytics import ticks_to_bars
from live_server.config import Settings
from live_server.db import DB
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5
from live_server.kiwoom_us_mock_broker import KiwoomUSMockBroker

ENGINE_NAME = 'ENGINE5_V22E_USA'
ENTRY_SCORE = float(os.getenv('V22E_US_ENTRY_SCORE', '50') or 50)
MAX_CANDIDATES = max(1, min(40, int(os.getenv('V22E_US_MAX_CANDIDATES', '20') or 20)))
QTY_DEFAULT = max(1, int(os.getenv('V22E_US_MOCK_QTY', os.getenv('DBB_MOCK_QTY', '2')) or 2))
CROSS_PCT = max(0.001, float(os.getenv('V22E_US_MOCK_CROSS_PCT', os.getenv('DBB_MOCK_CROSS_PCT', '0.01')) or 0.01))
FORCE_FLAT_MINUTE_ET = int(os.getenv('V22E_US_FORCE_FLAT_MINUTE_ET', str(15*60+55)) or (15*60+55))
RECON_SEC = max(30, int(os.getenv('V22E_US_RECON_SEC', '60') or 60))
LOOP_SEC = max(2, int(os.getenv('V22E_US_LOOP_SEC', '5') or 5))
STATE_PATH = Path(os.getenv('V22E_US_STATE_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_state.json'))
LOG_PATH = Path(os.getenv('V22E_US_LOG_PATH', '/home/ubuntu/day-trader-api/v22e_us_mock_live.jsonl'))
STATUS_URL = os.getenv('V22E_US_STATUS_URL', 'http://127.0.0.1:8000/api/v4/USA/status')
ET = ZoneInfo('America/New_York')

settings = Settings()
db = DB(settings.db_path)
engine = DoubleBollingerEngine5().with_entry_score(ENTRY_SCORE)
_attempted = set()
_last_recon = 0.0
_holdings_cache = {}


def truthy(name: str, default: str = '0') -> bool:
    return str(os.getenv(name, default)).lower() in {'1','true','yes','on'}


def log(event: str, **payload):
    row = {'ts': datetime.now(timezone.utc).isoformat(), 'event': event, 'engine': ENGINE_NAME, **payload}
    text = json.dumps(row, ensure_ascii=False, default=str)
    print(text, flush=True)
    try:
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(text + '\n')
    except Exception:
        pass


def load_state():
    try:
        d = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(d):
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    tmp.replace(STATE_PATH)


def f(v, default=0.0):
    try:
        x = float(v)
        return default if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return default


def i(v, default=0):
    try:
        return int(float(str(v).replace(',', '')))
    except Exception:
        return default


def session():
    now = datetime.now(timezone.utc).astimezone(ET)
    minute = now.hour * 60 + now.minute
    regular = now.weekday() < 5 and 9*60+30 <= minute < 16*60
    return now, minute, regular


def completed_5m(symbol: str):
    b = ticks_to_bars(db.ticks(symbol, 12000), 5)
    if b is None or len(b) < 2:
        return None
    x = b.copy().reset_index(drop=True)
    try:
        import pandas as pd
        ts = pd.to_datetime(x['time'], errors='coerce', utc=True)
        now_bucket = pd.Timestamp.now(tz='UTC').floor('5min')
        if len(x) and pd.notna(ts.iloc[-1]) and ts.iloc[-1].floor('5min') >= now_bucket:
            x = x.iloc[:-1].reset_index(drop=True)
    except Exception:
        x = x.iloc[:-1].reset_index(drop=True)
    return x if len(x) >= 30 else None


def evaluate_entry(b5):
    z = engine.enrich(b5)
    if z.empty:
        return {'enter': False, 'reason': 'NO_ENGINE_ROWS'}
    r = z.iloc[-1]
    px = f(r.get('close')); il = f(r.get('inner_lower')); ou = f(r.get('outer_upper'))
    band_r = max(px - il, 0.0) if px and il else 0.0
    score = f(r.get('entry_score'))
    enter = bool(r.get('entry_signal')) and band_r > 0
    return {
        'enter': enter, 'reason': 'V22E_ENTRY' if enter else 'NO_ENTRY',
        'score': score, 'effective_score': score, 'price': px,
        'bar_time': str(r.get('time') or ''), 'band_r': band_r,
        'stop_price': px - band_r if band_r else 0.0,
        'tp1_price': px + 2*band_r if band_r else 0.0,
        'outer_upper': ou,
    }


def evaluate_exit(b5, pos):
    z = engine.enrich(b5)
    if z.empty:
        return {'exit': False, 'reason': 'NO_ENGINE_ROWS'}
    r = z.iloc[-1]
    qty = i(pos.get('qty')); px = f(r.get('close'))
    stop = f(pos.get('stop_price')); tp1 = f(pos.get('tp1_price'))
    tp1_done = bool(pos.get('tp1_done')); outer_done = bool(pos.get('outer_reduced'))
    ou = f(r.get('outer_upper')); il = f(r.get('inner_lower'))
    if qty <= 0:
        return {'exit': False, 'reason': 'NO_POSITION'}
    if stop and px <= stop:
        return {'exit': True, 'sell_qty': qty, 'reason': 'V22E_STOP_-1R', 'price': px}
    if not tp1_done and tp1 and px >= tp1:
        return {'exit': True, 'sell_qty': max(1, qty//2), 'reason': 'V22E_TP1_+2R_50PCT', 'price': px, 'tp1_done': True}
    if tp1_done and not outer_done and ou and f(r.get('high')) >= ou:
        return {'exit': True, 'sell_qty': max(1, qty//2), 'reason': 'V22E_RUNNER_OUTER_UPPER_HALF', 'price': px, 'outer_reduced': True}
    fade = sum(1 for x in (f(r.get('mid_slope8')) <= 0, f(r.get('macd_slope_spread')) <= 0, f(r.get('rsi_slope')) <= 0) if x)
    if tp1_done and fade >= 2:
        return {'exit': True, 'sell_qty': qty, 'reason': 'V22E_RUNNER_MOMENTUM_FADE_2OF3', 'price': px}
    if tp1_done and il and px < il:
        return {'exit': True, 'sell_qty': qty, 'reason': 'V22E_RUNNER_INNER_LOWER_CLOSE', 'price': px}
    return {'exit': False, 'reason': 'HOLD', 'price': px, 'fade_count': fade}


def marketable(price: float, side: str):
    px = price * (1 + CROSS_PCT) if side == 'BUY' else price * (1 - CROSS_PCT)
    return round(px, 2 if px >= 1 else 4)


def broker():
    b = KiwoomUSMockBroker()
    if 'mockapi.kiwoom.com' not in b.cfg.rest_base:
        raise RuntimeError('REFUSE_NON_MOCK_US_BROKER')
    return b


def parse_holding(x, exchange):
    sym = str(x.get('stk_cd') or '').upper().strip()
    qty = i(x.get('sell_alowq') or x.get('poss_qty') or 0)
    if not sym or qty <= 0:
        return None
    return {
        'symbol': sym, 'exchange': exchange, 'qty': qty,
        'avg': f(x.get('frgn_stk_book_uv')), 'price': f(x.get('now_pric')),
    }


def refresh_holdings(force=False):
    global _last_recon, _holdings_cache
    now = time.monotonic()
    if not force and now - _last_recon < RECON_SEC:
        return dict(_holdings_cache)
    out = {}
    b = broker()
    for idx, ex in enumerate(('NY','ND','NA')):
        try:
            r = b.balance('', ex)
            for x in r.get('result_list') or []:
                h = parse_holding(x, ex)
                if h:
                    out[h['symbol']] = h
        except Exception as e:
            log('ACCOUNT_READ_ERROR', exchange=ex, error=repr(e))
        if idx < 2:
            time.sleep(0.8)
    _holdings_cache = out
    _last_recon = time.monotonic()
    return dict(out)


def finder_symbols():
    found = []
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=2) as r:
            data = json.loads(r.read().decode('utf-8'))
        def walk(v):
            if isinstance(v, dict):
                sym = str(v.get('symbol') or '').upper().strip()
                if sym and 1 <= len(sym) <= 8 and sym not in found:
                    found.append(sym)
                for vv in v.values():
                    walk(vv)
            elif isinstance(v, list):
                for vv in v:
                    walk(vv)
        walk(data)
    except Exception as e:
        log('FINDER_STATUS_ERROR', error=repr(e))
    if not found:
        for sym in settings.core_symbols + settings.symbols:
            s = str(sym).upper()
            if s not in found:
                found.append(s)
    return found[:MAX_CANDIDATES]


def reconstruct_meta(sym, holding, b5):
    avg = f(holding.get('avg')) or f(holding.get('price')) or f(b5.iloc[-1].get('close'))
    z = engine.enrich(b5); r = z.iloc[-1]
    il = f(r.get('inner_lower')); band_r = max(avg - il, 0.0) if il else 0.0
    if band_r <= 0:
        band_r = max(avg * 0.01, 0.01)
    return {
        'symbol': sym, 'exchange': holding.get('exchange') or settings.exchange_for(sym),
        'entry_price': avg, 'stop_price': avg-band_r, 'tp1_price': avg+2*band_r,
        'tp1_done': False, 'outer_reduced': False, 'reconciled_from_kiwoom': True,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }


def order_once(side, sym, qty, signal_px, exchange, bar_key, reason):
    key = (side, sym, str(bar_key), reason)
    if key in _attempted:
        return {'ok': False, 'reason': 'SAME_BAR_ATTEMPT_BLOCKED'}
    _attempted.add(key)
    limit_px = marketable(signal_px, side)
    b = broker()
    log('ORDER_ATTEMPT', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason)
    try:
        if side == 'BUY':
            ack = b.buy_limit(sym, qty, limit_px, exchange)
        else:
            ack = b.sell_limit(sym, qty, limit_px, exchange)
        log('ORDER_ACCEPTED', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason, return_code=ack.get('return_code'))
        return {'ok': True, 'ack': ack, 'limit': limit_px}
    except Exception as e:
        log('ORDER_FAILED_NO_RETRY', side=side, symbol=sym, qty=qty, limit=limit_px, exchange=exchange, reason=reason, error=repr(e))
        return {'ok': False, 'reason': 'BROKER_ERROR_NO_RETRY', 'error': repr(e)}


def main():
    if not truthy('V22E_US_MOCK_AUTO'):
        raise SystemExit('V22E_US_MOCK_AUTO is not enabled')
    if truthy('WILLIAMS_KIWOOM_US_MOCK_AUTO'):
        raise SystemExit('REFUSE_DUAL_AUTHORITY_WILLIAMS')
    if truthy('DBB_MOCK_AUTO'):
        raise SystemExit('REFUSE_DUAL_AUTHORITY_LEGACY_DBB_PAIR')
    if not truthy('KIWOOM_MOCK_US_ORDER_ENABLE'):
        raise SystemExit('KIWOOM_MOCK_US_ORDER_ENABLE is not enabled')

    b = broker()
    # Safe read-only connectivity validation before the loop.
    test = b.balance('SPY', 'NY')
    log('BROKER_CONNECTED', broker='KIWOOM_US_MOCK_ONLY', rest_base=b.cfg.rest_base, account_api='ust21070', order_enabled=b.cfg.order_enable, read_return_code=test.get('return_code'))
    log('AUTHORITY', US_BUY_AUTHORITY=ENGINE_NAME, US_SELL_AUTHORITY=ENGINE_NAME, WILLIAMS_US_ORDER_AUTHORITY='DISABLED', LEGACY_DBB_PAIR_ORDER_AUTHORITY='DISABLED', INTERNAL_PAPER_EXECUTION='DISABLED')

    state = load_state()
    last_bar = {}
    while True:
        try:
            now_et, et_min, regular = session()
            holdings = refresh_holdings()

            # Remove stale strategy metadata only after broker confirms flat.
            for sym in list(state):
                if sym not in holdings:
                    state.pop(sym, None)
                    save_state(state)
                    log('STATE_CLEARED_BROKER_FLAT', symbol=sym)

            symbols = list(holdings)
            for sym in finder_symbols():
                if sym not in symbols:
                    symbols.append(sym)
            symbols = symbols[:max(MAX_CANDIDATES, len(holdings))]

            for sym in symbols:
                b5 = completed_5m(sym)
                if b5 is None:
                    continue
                bar_key = str(b5.iloc[-1].get('time') or '')
                if last_bar.get(sym) == bar_key:
                    continue
                last_bar[sym] = bar_key
                px = f(b5.iloc[-1].get('close'))
                h = holdings.get(sym)

                if h:
                    meta = state.get(sym)
                    if not meta:
                        meta = reconstruct_meta(sym, h, b5)
                        state[sym] = meta; save_state(state)
                        log('RECONCILED_FROM_KIWOOM', symbol=sym, qty=h['qty'], avg=h['avg'], exchange=h['exchange'])
                    pos = {**meta, 'qty': h['qty']}
                    d = evaluate_exit(b5, pos)
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
                    ex = settings.exchange_for(sym)
                    res = order_once('BUY', sym, QTY_DEFAULT, px, ex, bar_key, 'V22E_ENTRY')
                    if res.get('ok'):
                        state[sym] = {
                            'symbol': sym, 'exchange': ex, 'entry_price': px,
                            'stop_price': d.get('stop_price'), 'tp1_price': d.get('tp1_price'),
                            'tp1_done': False, 'outer_reduced': False,
                            'entry_score': d.get('effective_score'), 'entry_bar': bar_key,
                            'updated_at': datetime.now(timezone.utc).isoformat(),
                        }
                        save_state(state); _last_recon = 0.0
            time.sleep(LOOP_SEC)
        except Exception as e:
            log('LOOP_ERROR', error=repr(e))
            time.sleep(max(LOOP_SEC, 5))

if __name__ == '__main__':
    main()
'''

SERVICE_TEXT = '''[Unit]\nDescription=DAY TRADER V22E US Kiwoom MOCK\nAfter=network-online.target day-trader-api.service\nWants=network-online.target\n\n[Service]\nType=simple\nUser=ubuntu\nWorkingDirectory=/home/ubuntu/day-trader-api\nEnvironmentFile=/home/ubuntu/day-trader-api/.env\nExecStart=/home/ubuntu/day-trader-api/venv/bin/python -m live_server.v22e_us_mock_live\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n'''


def run(*args, check=True):
    print('+', ' '.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=check, text=True)


def sudo_read(path: Path) -> str:
    return subprocess.check_output(['sudo', 'cat', str(path)], text=True)


def install_text(dst: Path, text: str, mode='0644'):
    fd, name = tempfile.mkstemp(prefix='v22e_us_', suffix=dst.suffix or '.tmp')
    os.close(fd)
    p = Path(name)
    try:
        p.write_text(text, encoding='utf-8')
        if dst.suffix == '.py':
            py_compile.compile(str(p), doraise=True)
        run('sudo', 'install', '-m', mode, p, dst)
    finally:
        p.unlink(missing_ok=True)


def set_env(text: str, key: str, value: str) -> str:
    pat = re.compile(rf'(?m)^\s*{re.escape(key)}\s*=.*$')
    line = f'{key}={value}'
    if pat.search(text):
        return pat.sub(line, text, count=1)
    if text and not text.endswith('\n'):
        text += '\n'
    return text + line + '\n'


def validate_env(text: str):
    keys = {}
    for raw in text.splitlines():
        if '=' not in raw or raw.lstrip().startswith('#'):
            continue
        k, v = raw.split('=', 1); keys[k.strip()] = v.strip()
    if not ((keys.get('KIWOOM_US_MOCK_APP_KEY') and keys.get('KIWOOM_US_MOCK_APP_SECRET')) or (keys.get('KIWOOM_MOCK_APP_KEY') and keys.get('KIWOOM_MOCK_APP_SECRET'))):
        raise SystemExit('ABORT US mock credentials missing')
    base = keys.get('KIWOOM_US_MOCK_REST_BASE') or keys.get('KIWOOM_MOCK_REST_BASE') or 'https://mockapi.kiwoom.com'
    if 'mockapi.kiwoom.com' not in base:
        raise SystemExit('ABORT refusing non-mock US REST base')


def wait_http(url: str, seconds=60):
    deadline = time.time() + seconds; last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            last = e
        time.sleep(2)
    raise SystemExit(f'ABORT HTTP health failed: {last}')


def main():
    if not ENV.exists() or not BROKER_SRC.exists():
        raise SystemExit('ABORT required runtime/env/broker file missing')

    env = sudo_read(ENV)
    validate_env(env)
    env = set_env(env, 'WILLIAMS_KIWOOM_US_MOCK_AUTO', '0')
    env = set_env(env, 'DBB_MOCK_AUTO', '0')
    env = set_env(env, 'V22E_US_MOCK_AUTO', '1')
    env = set_env(env, 'KIWOOM_MOCK_US_ORDER_ENABLE', '1')
    install_text(ENV, env, '0600')
    print('US_ENV_AUTHORITY=V22E_ONLY', flush=True)

    # Keep the existing validated US mock adapter in runtime.
    py_compile.compile(str(BROKER_SRC), doraise=True)
    run('sudo', 'install', '-m', '0644', BROKER_SRC, BROKER_DST)
    install_text(RUNNER_DST, RUNNER)
    install_text(SERVICE, SERVICE_TEXT)

    # Kill the old standalone pair runner if it survived from Friday.
    subprocess.run(['sudo', 'pkill', '-f', 'dbb_pair_mock_live.py'], check=False)
    # Disable known legacy unit names only when present; never fail deployment on absence.
    for unit in ('day-trader-dbb-mock.service', 'dbb-pair-mock-live.service', 'day-trader-us-mock.service'):
        subprocess.run(['sudo', 'systemctl', 'disable', '--now', unit], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    run('sudo', 'systemctl', 'daemon-reload')
    # Restart API so any embedded legacy Williams code reloads the disabled env flag.
    run('sudo', 'systemctl', 'restart', API_SERVICE)
    wait_http('http://127.0.0.1:8000/health', 60)
    print('API_HEALTH=PASS', flush=True)

    run('sudo', 'systemctl', 'enable', '--now', V22E_SERVICE)
    time.sleep(4)
    active = subprocess.check_output(['sudo', 'systemctl', 'is-active', V22E_SERVICE], text=True).strip()
    if active != 'active':
        subprocess.run(['sudo', 'systemctl', 'status', V22E_SERVICE, '--no-pager', '-l'], check=False)
        subprocess.run(['sudo', 'journalctl', '-u', V22E_SERVICE, '-n', '80', '--no-pager'], check=False)
        raise SystemExit('ABORT V22E service not active')

    # Require the read-only broker connectivity marker before claiming connected.
    deadline = time.time() + 45; journal = ''
    while time.time() < deadline:
        journal = subprocess.check_output(['sudo', 'journalctl', '-u', V22E_SERVICE, '-n', '80', '--no-pager'], text=True)
        if 'BROKER_CONNECTED' in journal and 'AUTHORITY' in journal:
            break
        time.sleep(2)
    else:
        print(journal)
        raise SystemExit('ABORT broker connectivity marker missing')

    if 'REFUSE_DUAL_AUTHORITY' in journal:
        print(journal)
        raise SystemExit('ABORT dual authority guard triggered')

    print('V22E_US_SERVICE=ACTIVE', flush=True)
    print('US_BUY_AUTHORITY=ENGINE5_V22E_USA', flush=True)
    print('US_SELL_AUTHORITY=ENGINE5_V22E_USA', flush=True)
    print('US_BROKER=KIWOOM_US_MOCK_ONLY', flush=True)
    print('US_REST_BASE=https://mockapi.kiwoom.com', flush=True)
    print('US_ACCOUNT_API=ust21070', flush=True)
    print('US_BUY_API=ust20000', flush=True)
    print('US_SELL_API=ust20001', flush=True)
    print('WILLIAMS_US_ORDER_AUTHORITY=DISABLED', flush=True)
    print('LEGACY_DBB_PAIR_ORDER_AUTHORITY=DISABLED', flush=True)
    print('INTERNAL_PAPER_EXECUTION=DISABLED', flush=True)
    print('AUTO_ORDER_RETRY=DISABLED_PER_ENGINE_BAR', flush=True)
    print('DEPLOY=PASS', flush=True)

if __name__ == '__main__':
    main()
