from __future__ import annotations

"""Deploy KR Engine5 V22 as the live Kiwoom MOCK entry authority.

Fail closed.  The script copies the V22 modules into runtime, rewires the KR mock
order bridge so Williams no longer has BUY authority, compiles, restarts, waits
for health, and verifies the runtime source contains the V22 authority marker.
"""

from pathlib import Path
import py_compile
import shutil
import subprocess
import time
import urllib.request

REPO = Path('/home/ubuntu/day-trader-api-repo')
RUNTIME = Path('/home/ubuntu/day-trader-api')
SERVICE = 'day-trader-api'

FILES = [
    'live_server/double_bollinger_engine5.py',
    'live_server/engine5_v22_entry_policy.py',
    'live_server/engine5_v22_kr.py',
    'live_server/engine5_v22_live_kr.py',
]

OLD_ENTRY = '            entry=bool(row.get("williams_entry") or row.get("williams_signal_entry"))\n'
NEW_ENTRY = '''            # V22_KR_ORDER_AUTHORITY: Williams is telemetry only.\n            # Actual Kiwoom MOCK BUY permission comes only from causal Engine5 V22.\n            from live_server.engine5_v22_live_kr import evaluate_entry as _v22_kr_entry\n            _v22_decision=_v22_kr_entry(row)\n            row['engine5_v22_decision']=_v22_decision\n            entry=bool(_v22_decision.get('enter'))\n'''

OLD_BUY_STATE = '''                self._last[key]={\n                    "in_pos":True,\n                    "buy_order_no":order_no,\n                    "qty":qty,\n                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                    "entered_bar_time":_dt.now(_WILLIAMS_KST).strftime('%Y%m%d%H%M%S'),\n                }\n'''
NEW_BUY_STATE = '''                self._last[key]={\n                    "in_pos":True,\n                    "buy_order_no":order_no,\n                    "qty":qty,\n                    "entry_price":price,\n                    "entered_ts":_time.time(),\n                    "entered_bar_time":_dt.now(_WILLIAMS_KST).strftime('%Y%m%d%H%M%S'),\n                    "engine":"ENGINE5_V22_KR_LIVE",\n                    "engine5_v22_decision":_v22_decision,\n                    "stop_price":_f(_v22_decision.get("stop_price")),\n                    "tp1_price":_f(_v22_decision.get("tp1_price")),\n                    "tp1_done":False,\n                }\n'''


def run(*args):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n == 0 and new in text:
        print(f'{label}=ALREADY_PATCHED', flush=True)
        return text
    if n != 1:
        raise SystemExit(f'ABORT {label}: expected exactly one anchor, found {n}')
    print(f'{label}=PATCHED', flush=True)
    return text.replace(old, new, 1)


def main():
    for rel in FILES:
        src = REPO / rel
        dst = RUNTIME / rel
        if not src.exists():
            raise SystemExit(f'ABORT missing source: {src}')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        py_compile.compile(str(dst), doraise=True)
        print('INSTALLED', rel, flush=True)

    target = RUNTIME / 'live_server/v4_engine.py'
    text = target.read_text()
    backup = target.with_suffix('.py.pre_engine5_v22_kr')
    if not backup.exists():
        shutil.copy2(target, backup)
        print('BACKUP', backup, flush=True)

    text = replace_once(text, OLD_ENTRY, NEW_ENTRY, 'V22_ORDER_AUTHORITY')
    text = replace_once(text, OLD_BUY_STATE, NEW_BUY_STATE, 'V22_POSITION_STATE')
    target.write_text(text)
    py_compile.compile(str(target), doraise=True)

    # Static authority verification: the legacy Williams boolean must no longer
    # be the executable entry assignment in runtime.
    verify = target.read_text()
    if 'entry=bool(row.get("williams_entry") or row.get("williams_signal_entry"))' in verify:
        raise SystemExit('ABORT legacy Williams BUY authority still present')
    if "entry=bool(_v22_decision.get('enter'))" not in verify:
        raise SystemExit('ABORT V22 BUY authority marker missing')

    code = (
        "from live_server.engine5_v22_kr import VERSION,MARKET; "
        "from live_server.engine5_v22_live_kr import ENGINE_NAME,evaluate_entry; "
        "assert VERSION=='V22' and MARKET=='KR'; "
        "print('KR_ORDER_ENGINE=',ENGINE_NAME)"
    )
    run(RUNTIME / 'venv/bin/python', '-c', code)

    run('sudo', 'systemctl', 'restart', SERVICE)
    deadline = time.time() + 60
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as r:
                last = r.read().decode('utf-8', 'replace')
                if r.status == 200:
                    print('HEALTH=PASS', flush=True)
                    print(last, flush=True)
                    break
        except Exception as e:
            last = repr(e)
        time.sleep(2)
    else:
        raise SystemExit(f'ABORT health failed: {last}')

    print('KR_ORDER_AUTHORITY=ENGINE5_V22_KR_LIVE', flush=True)
    print('WILLIAMS_BUY_AUTHORITY=DISABLED', flush=True)
    print('DEPLOY=PASS', flush=True)


if __name__ == '__main__':
    main()
