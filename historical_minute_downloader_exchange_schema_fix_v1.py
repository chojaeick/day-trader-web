#!/usr/bin/env python3
from pathlib import Path

P=Path('/home/ubuntu/day-trader-api/tools/historical_minute_downloader.py')

OLD='''    row = con.execute("""
        SELECT exchange
        FROM daily_metrics
        WHERE symbol=?
          AND exchange IS NOT NULL
          AND exchange<>''
        LIMIT 1
    """, (symbol,)).fetchone()

    if row:
        return str(row[0])
'''

NEW='''    # Schema-compatible fallback: some DB versions do not have daily_metrics.exchange.
    dm_cols = {r[1] for r in con.execute("PRAGMA table_info(daily_metrics)").fetchall()}
    if "exchange" in dm_cols:
        row = con.execute("""
            SELECT exchange
            FROM daily_metrics
            WHERE symbol=?
              AND exchange IS NOT NULL
              AND exchange<>''
            LIMIT 1
        """, (symbol,)).fetchone()
        if row:
            return str(row[0])
'''


def main():
    s=P.read_text()
    if 'Schema-compatible fallback: some DB versions do not have daily_metrics.exchange.' in s:
        print('HIST_DOWNLOADER_EXCHANGE_SCHEMA_FIX_V1_ALREADY_APPLIED')
        return
    if OLD not in s:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: daily_metrics exchange fallback')
    s=s.replace(OLD,NEW,1)
    P.write_text(s)
    print('HIST_DOWNLOADER_EXCHANGE_SCHEMA_FIX_V1_OK')

if __name__=='__main__':
    main()
