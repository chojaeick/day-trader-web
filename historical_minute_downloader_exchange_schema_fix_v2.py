#!/usr/bin/env python3
from pathlib import Path
import re

P=Path('/home/ubuntu/day-trader-api/tools/historical_minute_downloader.py')
s=P.read_text()

start=s.find('def resolve_exchange(con, symbol):')
if start < 0:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: resolve_exchange start')

m=re.search(r'\ndef [A-Za-z_][A-Za-z0-9_]*\(', s[start+1:])
if not m:
    raise SystemExit('PATCH_TARGET_NOT_FOUND: next function after resolve_exchange')
end=start+1+m.start()+1

new_func='''def resolve_exchange(con, symbol):
    symbol=str(symbol).strip()

    def has_column(table, column):
        try:
            cols=[str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
            return column in cols
        except Exception:
            return False

    # Existing primary source. Preserve behavior when schema supports it.
    if has_column("quotes", "exchange"):
        try:
            row=con.execute("""
                SELECT exchange
                FROM quotes
                WHERE symbol=?
                  AND exchange IS NOT NULL
                  AND exchange<>''
                ORDER BY updated_at DESC
                LIMIT 1
            """, (symbol,)).fetchone()
            if row:
                return str(row[0])
        except Exception:
            pass

    # Legacy fallback only when the column actually exists.
    if has_column("daily_metrics", "exchange"):
        try:
            row=con.execute("""
                SELECT exchange
                FROM daily_metrics
                WHERE symbol=?
                  AND exchange IS NOT NULL
                  AND exchange<>''
                LIMIT 1
            """, (symbol,)).fetchone()
            if row:
                return str(row[0])
        except Exception:
            pass

    # Six-digit numeric symbols are KRX instruments in this project.
    # Kiwoom downloader only needs a non-US exchange classification here.
    if symbol.isdigit() and len(symbol)==6:
        return "KRX"

    # Preserve existing USA default behavior for ordinary ticker symbols.
    return "NASDAQ"
'''

s2=s[:start]+new_func+s[end:]
P.write_text(s2)
print('HISTORICAL_MINUTE_DOWNLOADER_EXCHANGE_SCHEMA_FIX_V2_OK')
