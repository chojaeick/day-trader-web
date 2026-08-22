#!/usr/bin/env python3
"""
DAY TRADER V4 — DATA READINESS AUDIT V1

Purpose:
Confirm that the data foundation is complete BEFORE further engine validation.

NO DOWNLOAD.
NO STRATEGY SIMULATION.

Checks:
- historical_minute_bars coverage by symbol/date/exchange/source
- US/KR presence and date ranges
- OHLCV completeness / nulls / duplicate timestamps
- replay-cache coverage and columns
- classify indicators as PRESENT / DERIVABLE / MISSING_SOURCE
- explicitly flag whether KR historical minute data is ready for Monday prototype
"""

from pathlib import Path
import sqlite3
import glob
import os
import pandas as pd

ROOT = Path("/home/ubuntu/day-trader-api")
DB = ROOT / "daytrader.db"
CACHE_GLOB = "/tmp/fast_replay_cache/*.csv"
OUT = Path("/tmp/day_trader_data_readiness_audit.txt")

DERIVABLE_FROM_OHLCV = {
    "MACD": ["close"],
    "DYNAMIC_RSI": ["close"],
    "ATR": ["high","low","close"],
    "RSI": ["close"],
    "EMA": ["close"],
    "SMA": ["close"],
    "VWAP": ["high","low","close","volume"],
    "MFI": ["high","low","close","volume"],
    "RVOL": ["volume"],
    "VO": ["volume"],
}

OPTIONAL_MICROSTRUCTURE = [
    "bid", "ask", "bid_size", "ask_size",
    "trade_strength", "orderflow", "market_depth"
]

def qdf(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)

def main():
    out=[]
    add=out.append

    add("===== DAY TRADER V4 DATA READINESS AUDIT V1 =====")
    add("NO_DOWNLOAD True")
    add("NO_SIMULATION True")
    add("")

    if not DB.exists():
        add("FATAL DB_MISSING")
        OUT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return

    con=sqlite3.connect(DB)

    tables=qdf(con, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    add("===== 1. DB TABLES =====")
    add(",".join(tables["name"].astype(str)))
    add("")

    target="historical_minute_bars"
    if target not in set(tables["name"]):
        add("FATAL historical_minute_bars MISSING")
        con.close()
        OUT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return

    cols=qdf(con, f"PRAGMA table_info({target})")
    colset=set(cols["name"].astype(str))
    add("===== 2. HISTORICAL MINUTE SCHEMA =====")
    add(",".join(cols["name"].astype(str)))
    add("")

    required={"symbol","exchange","trade_date","interval_min","ts","open","high","low","close","volume"}
    add("RAW_REQUIRED_COLUMNS_OK " + str(required.issubset(colset)))
    if not required.issubset(colset):
        add("MISSING_RAW_COLUMNS " + ",".join(sorted(required-colset)))
    add("")

    cov=qdf(con, """
        SELECT
          exchange,
          source,
          COUNT(*) rows,
          COUNT(DISTINCT symbol) symbols,
          COUNT(DISTINCT trade_date) dates,
          MIN(trade_date) min_date,
          MAX(trade_date) max_date
        FROM historical_minute_bars
        WHERE interval_min=1
        GROUP BY exchange, source
        ORDER BY rows DESC
    """)
    add("===== 3. COVERAGE BY EXCHANGE / SOURCE =====")
    add(cov.to_string(index=False) if len(cov) else "NONE")
    add("")

    syms=qdf(con, """
        SELECT
          symbol, exchange,
          COUNT(*) rows,
          COUNT(DISTINCT trade_date) dates,
          MIN(trade_date) min_date,
          MAX(trade_date) max_date
        FROM historical_minute_bars
        WHERE interval_min=1
        GROUP BY symbol, exchange
        ORDER BY exchange, dates DESC, symbol
    """)
    add("===== 4. SYMBOL COVERAGE =====")
    add(syms.to_string(index=False) if len(syms) else "NONE")
    add("")

    # Heuristic market classification from exchange/source.
    # US exchanges in this project are ND/NY/NA; KR typically uses KR/KRX or Korean-specific sources.
    us_ex = {"ND","NY","NA","NASDAQ","NYSE","AMEX"}
    syms["market_guess"] = syms["exchange"].astype(str).str.upper().map(
        lambda x: "US" if x in us_ex else "KR_OR_OTHER"
    )

    us_rows=syms[syms.market_guess=="US"]
    kr_rows=syms[syms.market_guess=="KR_OR_OTHER"]

    add("===== 5. MARKET READINESS =====")
    add(f"US_SYMBOLS {len(us_rows)}")
    add(f"US_DATES {int(us_rows['dates'].sum()) if len(us_rows) else 0}")
    add(f"KR_OR_OTHER_SYMBOLS {len(kr_rows)}")
    add(f"KR_OR_OTHER_DATES {int(kr_rows['dates'].sum()) if len(kr_rows) else 0}")
    if len(kr_rows):
        add("KR_OR_OTHER_DETAIL")
        add(kr_rows.to_string(index=False))
    add("")

    quality=qdf(con, """
        SELECT
          COUNT(*) total_rows,
          SUM(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL THEN 1 ELSE 0 END) null_ohlcv,
          SUM(CASE WHEN open<=0 OR high<=0 OR low<=0 OR close<=0 THEN 1 ELSE 0 END) nonpositive_price
        FROM historical_minute_bars
        WHERE interval_min=1
    """)
    dup=qdf(con, """
        SELECT COUNT(*) duplicate_groups
        FROM (
          SELECT symbol, trade_date, interval_min, ts, COUNT(*) n
          FROM historical_minute_bars
          WHERE interval_min=1
          GROUP BY symbol, trade_date, interval_min, ts
          HAVING COUNT(*) > 1
        )
    """)
    add("===== 6. RAW DATA QUALITY =====")
    add(quality.to_string(index=False))
    add(dup.to_string(index=False))
    add("")

    cache_files=sorted(glob.glob(CACHE_GLOB))
    cache_cols=set()
    readable=0
    for p in cache_files[:50]:
        try:
            x=pd.read_csv(p,nrows=1)
            cache_cols.update(x.columns)
            readable+=1
        except Exception:
            pass

    add("===== 7. CACHE READINESS =====")
    add(f"CACHE_FILES {len(cache_files)}")
    add(f"CACHE_SCHEMAS_SAMPLED {readable}")
    add("CACHE_COLUMNS " + ",".join(sorted(cache_cols)))
    add("")

    add("===== 8. FEATURE CLASSIFICATION =====")
    for feat, prereq in DERIVABLE_FROM_OHLCV.items():
        exact_present = any(feat.lower() in c.lower() for c in cache_cols)
        if exact_present:
            status="PRESENT"
        elif all(c in cache_cols or c in colset for c in prereq):
            status="DERIVABLE"
        else:
            status="MISSING_SOURCE"
        add(f"{feat}: {status} | requires={','.join(prereq)}")

    add("")
    add("===== 9. OPTIONAL MICROSTRUCTURE =====")
    for f in OPTIONAL_MICROSTRUCTURE:
        present = f in cache_cols or f in colset
        add(f"{f}: {'PRESENT' if present else 'NOT_PRESENT'}")
    add("NOTE: these are not required for DRSI/MACD or OHLCV pattern tests, but may be required for future order-flow engines.")
    add("")

    raw_ok = (
        required.issubset(colset)
        and len(quality)
        and int(quality.iloc[0]["null_ohlcv"] or 0)==0
        and int(quality.iloc[0]["nonpositive_price"] or 0)==0
        and len(dup)
        and int(dup.iloc[0]["duplicate_groups"] or 0)==0
    )

    # Conservative: KR is "ready" only if we actually see non-US minute-history rows.
    kr_ready = len(kr_rows) > 0

    add("===== 10. DECISION =====")
    add(f"RAW_DATA_QUALITY_PASS {bool(raw_ok)}")
    add(f"US_HISTORICAL_READY {len(us_rows)>0}")
    add(f"KR_HISTORICAL_READY {kr_ready}")
    add(f"DERIVED_INDICATORS_NEED_DOWNLOAD False")
    if not kr_ready:
        add("ACTION: KR historical minute coverage is not confirmed. Inventory/download KR data BEFORE KR engine backtest.")
    else:
        add("ACTION: KR minute history exists; next verify sufficient symbol/date coverage for Monday prototype.")
    add("ACTION: Do not download MACD/Dynamic-RSI/ATR; calculate them from OHLCV.")

    con.close()
    OUT.write_text("\n".join(out),encoding="utf-8")
    print("\n".join(out))

if __name__=="__main__":
    main()
