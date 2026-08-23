from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import requests

from live_server.config import Settings
from live_server.db import DB
from live_server.kiwoom import KiwoomClient


def probe(k: KiwoomClient, s: Settings, symbol: str, exchange: str, api_id: str, days: int):
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y%m%d')
    body = {
        'stex_tp': exchange,
        'stk_cd': symbol,
        'strt_dt': start,
        'upd_stkpc_tp': '1',
        'exrt_appl_tp': '0',
    }
    r = requests.post(
        s.rest_base + '/api/us/chart',
        headers=k.headers(api_id),
        json=body,
        timeout=25,
    )
    try:
        d = r.json()
    except Exception:
        d = {}
    rows = d.get('result_list') or [] if isinstance(d, dict) else []
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    return {
        'api_id': api_id,
        'exchange': exchange,
        'http': r.status_code,
        'return_code': d.get('return_code') if isinstance(d, dict) else None,
        'return_msg': d.get('return_msg') if isinstance(d, dict) else None,
        'rows': len(rows) if isinstance(rows, list) else -1,
        'response_keys': sorted(d.keys()) if isinstance(d, dict) else [],
        'first_dt': first.get('dt') or first.get('date'),
        'first_close': first.get('cur_prc') or first.get('close_pric') or first.get('close'),
        'first_keys': sorted(first.keys()) if first else [],
        'cont_yn': r.headers.get('cont-yn') or r.headers.get('Cont-Yn'),
        'next_key': bool(r.headers.get('next-key') or r.headers.get('Next-Key')),
    }


def main():
    symbol = 'LENZ'
    s = Settings()
    db = DB(s.db_path)
    k = KiwoomClient(s, db)
    active = k.active_exchange(symbol)

    print('LENZ_US_CHART_PROBE_V38')
    print('active_exchange=' + str(active))

    tests = []
    # Current project path (daily), then native Kiwoom weekly/monthly TRs.
    for api_id, days in [('usa06012', 900), ('usa06013', 900), ('usa06014', 1400)]:
        try:
            tests.append(probe(k, s, symbol, active, api_id, days))
        except Exception as e:
            tests.append({'api_id': api_id, 'exchange': active, 'error': repr(e)})

    # If the configured exchange yields no monthly rows, verify all documented US exchange codes.
    monthly = next((x for x in tests if x.get('api_id') == 'usa06014'), {})
    if monthly.get('rows', 0) == 0:
        for ex in ('ND', 'NY', 'NA'):
            if ex == active:
                continue
            try:
                tests.append(probe(k, s, symbol, ex, 'usa06014', 1400))
            except Exception as e:
                tests.append({'api_id': 'usa06014', 'exchange': ex, 'error': repr(e)})

    for x in tests:
        print(json.dumps(x, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
