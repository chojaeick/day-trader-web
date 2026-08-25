#!/usr/bin/env python3
"""Williams V92 frozen structure-state candidate.

Purpose:
- Freeze the validated core behavior from V89-V91.
- Keep Williams entry semantics unchanged.
- HOLD while causal confirmed higher-low support survives.
- EXIT immediately when close breaks current support.
- No reentry, no indicator exit, no threshold retuning.

Diagnostic/offline candidate only. This file does NOT wire live orders.
"""
import argparse
import re
import sqlite3
import statistics

KR_RE = re.compile(r'^\d{6}$')


def rsi(vals, p):
    out = [None] * len(vals)
    if len(vals) < p + 2:
        return out
    gains, losses = [], []
    for i in range(1, p + 1):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = sum(gains) / p; al = sum(losses) / p
    out[p] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(p + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        g = max(d, 0.0); l = max(-d, 0.0)
        ag = (ag * (p - 1) + g) / p
        al = (al * (p - 1) + l) / p
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def load_days(con, symbol, max_days):
    ds = [r[0] for r in con.execute(
        "select distinct trade_date from historical_minute_bars where symbol=? and interval_min=1 order by trade_date desc limit ?",
        (symbol, max_days + 1),
    ).fetchall()]
    ds = sorted(ds)
    out = {}
    for d in ds:
        rows = con.execute(
            "select et_time,open,high,low,close,volume,ts from historical_minute_bars where symbol=? and trade_date=? and interval_min=1 order by et_time",
            (symbol, d),
        ).fetchall()
        if len(rows) >= 40:
            out[d] = rows
    return out


def pct(a, b):
    return (b / a - 1.0) * 100.0 if a else 0.0


def confirmed_swing_low(rows, confirm_i):
    """Return the newly confirmed swing-low index at confirm_i, else None.

    A swing low at j is only known causally at j+2, using bars j-2..j+2.
    """
    j = confirm_i - 2
    if j < 2 or j + 2 >= len(rows):
        return None
    lo = float(rows[j][3])
    if lo <= min(float(rows[k][3]) for k in range(j - 2, j + 3)):
        return j
    return None


def run_structure(rows, entry_i):
    entry = float(rows[entry_i][4])
    # Initial support = most recent causally confirmed swing low known at entry.
    support = None
    support_idx = None
    for i in range(4, entry_i + 1):
        j = confirmed_swing_low(rows, i)
        if j is not None:
            support = float(rows[j][3]); support_idx = j

    # If no confirmed swing exists, wait until one is causally confirmed.
    updates = 0
    exit_i = len(rows) - 1
    reason = 'EOD_STRUCTURE_HELD'
    peak = entry

    for i in range(entry_i + 1, len(rows)):
        peak = max(peak, float(rows[i][2]))
        j = confirmed_swing_low(rows, i)
        if j is not None:
            low_j = float(rows[j][3])
            if support is None or low_j > support:
                support = low_j; support_idx = j; updates += 1

        if support is not None and float(rows[i][4]) < support:
            exit_i = i; reason = 'SUPPORT_BREAK_EXIT'; break

    ret = pct(entry, float(rows[exit_i][4]))
    mfe = max(pct(entry, float(r[2])) for r in rows[entry_i:])
    mae = min(pct(entry, float(r[3])) for r in rows[entry_i:])
    cap = ret / mfe * 100.0 if mfe > 0 else 0.0
    return ret, mfe, mae, exit_i - entry_i, reason, cap, updates, support_idx


def metrics(name, trades):
    if not trades:
        print(name, 'N=0'); return
    vals = [x[2] for x in trades]
    gp = sum(x for x in vals if x > 0); gl = -sum(x for x in vals if x < 0)
    pf = gp / gl if gl else float('inf')
    med = statistics.median(vals)
    hold = statistics.fmean(x[5] for x in trades)
    print(f"{name} N={len(trades)} WIN={100*sum(x>0 for x in vals)/len(vals):.1f}% AVG_RET={statistics.fmean(vals):.3f}% PF={pf:.3f} MED={med:.3f}% HOLD={hold:.1f}m")
    big = [x for x in trades if x[3] >= 5]
    if big:
        print(f"BIG5 N={len(big)} RET_AVG={statistics.fmean(x[2] for x in big):.2f}% CAP_AVG={statistics.fmean(x[7] for x in big):.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='daytrader.db')
    ap.add_argument('--max-days', type=int, default=20)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    syms = [r[0] for r in con.execute(
        "select symbol from historical_minute_bars where interval_min=1 group by symbol order by count(*) desc"
    ).fetchall() if KR_RE.match(str(r[0] or ''))]

    entries = []
    for s in syms:
        dm = load_days(con, s, args.max_days)
        ds = sorted(dm)
        for di in range(1, len(ds)):
            d = ds[di]; prev = dm[ds[di - 1]]; cur = dm[d]
            closes = [float(r[4]) for r in cur]
            ph = max(float(r[2]) for r in prev); pl = min(float(r[3]) for r in prev)
            op = float(cur[0][1]); trig = op + 0.5 * (ph - pl)
            r2 = rsi(closes, 2); sig_i = None
            for i in range(15, len(cur) - 10):
                if closes[i - 1] <= trig < closes[i] and r2[i] is not None and r2[i] > 50:
                    sig_i = i; break
            if sig_i is not None and sig_i + 1 < len(cur):
                entries.append((d, s, cur, sig_i + 1, trig))

    dates = sorted(set(d for d, _, _, _, _ in entries))
    cut = len(dates) // 2
    is_dates = set(dates[:cut]); oos_dates = set(dates[cut:])

    trades = []
    for d, s, rows, ei, trig in entries:
        ret, mfe, mae, hold, reason, cap, updates, support_idx = run_structure(rows, ei)
        trades.append((d, s, ret, mfe, mae, hold, reason, cap, updates, support_idx, trig))

    print('=== WILLIAMS KOREA CORE STRUCTURE LIVE-CANDIDATE V92 ===')
    print('FROZEN: exact Williams next-bar entry; causal higher-low support; close-break EXIT; no reentry/indicator exit.')
    print('IS_DATES', ','.join(sorted(is_dates)))
    print('OOS_DATES', ','.join(sorted(oos_dates)))
    metrics('STRUCT0_ALL', trades)
    metrics('STRUCT0_IS', [x for x in trades if x[0] in is_dates])
    metrics('STRUCT0_OOS', [x for x in trades if x[0] in oos_dates])
    print('--- OOS TRADES ---')
    for x in [x for x in trades if x[0] in oos_dates]:
        print(f"{x[0]} {x[1]} RET={x[2]:.2f}% MFE={x[3]:.2f}% MAE={x[4]:.2f}% HOLD={x[5]}m CAP={x[7]:.1f}% UPDATES={x[8]} {x[6]}")
    con.close()

if __name__ == '__main__':
    main()
