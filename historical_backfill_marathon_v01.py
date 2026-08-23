#!/usr/bin/env python3
"""
Historical minute-bar backfill marathon v0.1

Purpose
- Long-running, resumable DB expansion for strategy validation.
- Uses the existing runtime historical*downloader.py one symbol/date at a time.
- Skips symbol/date pairs already present in historical_minute_bars.
- Never writes strategy/trade tables; only the existing downloader is allowed to write bars.
- Continues through holidays / empty days / transient failures and records a progress log.

Default universe follows the current project restriction:
  ETFs + S&P500 names only.
Legacy non-S&P names such as ARM/TSM are intentionally excluded from defaults.

Example:
  python -u historical_backfill_marathon_v01.py --start 20250102 --end 20260814
  python -u historical_backfill_marathon_v01.py --start 20240102 --end 20260814 --symbols NVDA,AMD,SPY,QQQ
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

RUNTIME = Path(os.environ.get("DAYTRADER_ROOT", "/home/ubuntu/day-trader-api"))
DB_PATH = Path(os.environ.get("DAYTRADER_DB", str(RUNTIME / "daytrader.db")))

# S&P500 / ETF validation set. Keep this deliberately compact for the first marathon.
DEFAULT_SYMBOLS = [
    "AMD", "AMZN", "AVGO", "GOOGL", "INTC", "NFLX", "NVDA", "ORCL", "PLTR", "SMCI",
    "SPY", "QQQ", "SMH", "SOXL", "SOXS", "TQQQ", "SQQQ",
]


def ymd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def weekdays(start: dt.date, end: dt.date):
    d = start
    one = dt.timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += one


def find_downloader() -> Path:
    candidates = []
    for base in [RUNTIME / "tools", RUNTIME]:
        if base.exists():
            candidates.extend(sorted(base.glob("historical*downloader.py")))
    if not candidates:
        raise FileNotFoundError(
            f"No historical*downloader.py found under {RUNTIME}/tools or {RUNTIME}"
        )
    return candidates[0]


def existing_pairs(conn: sqlite3.Connection, symbols: list[str], start: str, end: str) -> set[tuple[str, str]]:
    marks = ",".join("?" for _ in symbols)
    sql = f"""
        SELECT symbol, trade_date
        FROM historical_minute_bars
        WHERE symbol IN ({marks})
          AND trade_date BETWEEN ? AND ?
        GROUP BY symbol, trade_date
        HAVING COUNT(*) > 0
    """
    rows = conn.execute(sql, [*symbols, start, end]).fetchall()
    return {(str(s), str(d)) for s, d in rows}


def db_count(conn: sqlite3.Connection, symbol: str, date: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM historical_minute_bars WHERE symbol=? AND trade_date=?",
        (symbol, date),
    ).fetchone()
    return int(row[0] if row else 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20250102", help="YYYYMMDD")
    ap.add_argument("--end", default="20260814", help="YYYYMMDD")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--sleep", type=float, default=0.8, help="seconds between downloader calls")
    ap.add_argument("--timeout", type=int, default=180, help="per symbol/day subprocess timeout seconds")
    ap.add_argument("--max-fail", type=int, default=100000, help="safety stop after this many failed calls")
    ap.add_argument("--log", default="/tmp/historical_backfill_marathon_v01.log")
    args = ap.parse_args()

    start_d, end_d = ymd(args.start), ymd(args.end)
    if end_d < start_d:
        raise SystemExit("end < start")

    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    if not symbols:
        raise SystemExit("no symbols")

    py = RUNTIME / "venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    downloader = find_downloader()

    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        existing = existing_pairs(conn, symbols, args.start, args.end)
    except sqlite3.OperationalError as e:
        raise SystemExit(f"historical_minute_bars query failed: {e}")

    dates = [d.strftime("%Y%m%d") for d in weekdays(start_d, end_d)]
    todo = [(s, d) for s in symbols for d in dates if (s, d) not in existing]
    total_possible = len(symbols) * len(dates)

    print("===== HISTORICAL BACKFILL MARATHON v0.1 =====", flush=True)
    print("DB", DB_PATH, flush=True)
    print("DOWNLOADER", downloader, flush=True)
    print("RANGE", args.start, args.end, flush=True)
    print("SYMBOLS", len(symbols), ",".join(symbols), flush=True)
    print("WEEKDAYS", len(dates), "TOTAL_PAIRS", total_possible, flush=True)
    print("ALREADY_PRESENT", len(existing), "TODO", len(todo), flush=True)
    print("RESUMABLE True / HOLIDAYS MAY APPEAR AS EMPTY OR FAILED", flush=True)

    ok = empty = fail = timeout_n = 0
    t0 = time.time()
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n===== START {dt.datetime.now().isoformat()} RANGE={args.start}-{args.end} SYMBOLS={','.join(symbols)} TODO={len(todo)} =====\n")
        log.flush()

        for i, (sym, day) in enumerate(todo, 1):
            cmd = [str(py), str(downloader), sym, day]
            status = ""
            detail = ""
            try:
                p = subprocess.run(
                    cmd,
                    cwd=str(RUNTIME),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=args.timeout,
                    env={**os.environ, "PYTHONPATH": str(RUNTIME)},
                )
                # Re-open/check DB after the child exits; downloader may have committed rows.
                n = db_count(conn, sym, day)
                if n > 0:
                    ok += 1
                    status = f"OK rows={n}"
                elif p.returncode == 0:
                    empty += 1
                    status = "EMPTY"
                else:
                    fail += 1
                    status = f"FAIL rc={p.returncode}"
                detail = (p.stdout or "")[-1200:].replace("\n", " | ")
            except subprocess.TimeoutExpired:
                timeout_n += 1
                fail += 1
                status = f"TIMEOUT>{args.timeout}s"
            except Exception as e:
                fail += 1
                status = f"ERROR {type(e).__name__}: {e}"

            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0.0
            eta = (len(todo) - i) / rate if rate > 0 else 0.0
            line = (
                f"PROGRESS {i}/{len(todo)} {sym} {day} {status} "
                f"OK={ok} EMPTY={empty} FAIL={fail} TIMEOUT={timeout_n} "
                f"elapsed={elapsed/60:.1f}m eta={eta/60:.1f}m"
            )
            print(line, flush=True)
            log.write(line + "\n")
            if detail:
                log.write("  " + detail + "\n")
            log.flush()

            if fail >= args.max_fail:
                print("SAFETY STOP: max-fail reached", flush=True)
                break
            if args.sleep > 0:
                time.sleep(args.sleep)

    elapsed = time.time() - t0
    print("===== DONE =====", flush=True)
    print("OK", ok, "EMPTY", empty, "FAIL", fail, "TIMEOUT", timeout_n, flush=True)
    print("ELAPSED_MIN", round(elapsed / 60, 1), flush=True)
    print("LOG", log_path, flush=True)
    print("RERUN SAME COMMAND TO RESUME; EXISTING DB PAIRS WILL BE SKIPPED.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
