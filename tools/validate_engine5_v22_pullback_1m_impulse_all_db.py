from __future__ import annotations

"""Run the V22 pullback 1m-impulse diagnostic on the widest KR 1m dataset in daytrader.db.

Scope
-----
- Reads *all* interval_min=1 rows from historical_minute_bars.
- Keeps Korean-style 6-digit numeric symbols only.
- If a symbol exists under multiple sources, uses the source with the largest
  row count for that symbol so duplicate feeds are not mixed.
- Reuses the current pullback logic unchanged and compares only the two promising
  1m impulse thresholds: 0.7% and 1.0%.
- Reports VETO15_ONLY and LOSING_EXIT_ONLY independently.

Diagnostic only. Production V22 is not modified.
"""

from dataclasses import replace
from pathlib import Path
import re
import sqlite3
import pandas as pd

import tools.validate_engine5_v22_pullback_1m_impulse_sweep as imp
import tools.validate_engine5_v22_uptrend_pullback_reentry as pb
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_pullback_1m_impulse_all_db')
IMPULSE_PCTS = [0.70, 1.00]
GROUPS = {
    'VETO15_ONLY': ['VETO15'],
    'LOSING_EXIT_ONLY': ['LOSING_EXIT'],
}
MIN_BARS = 80


def is_kr_symbol(x: str) -> bool:
    s = str(x).strip()
    return bool(re.fullmatch(r'\d{6}', s))


def load_all_kr_1m():
    con = sqlite3.connect(DB)
    src = pd.read_sql_query(
        """select source, session, interval_min, count(*) as rows,
                  count(distinct symbol) as symbols, min(ts) as min_ts, max(ts) as max_ts
           from historical_minute_bars
           group by source, session, interval_min
           order by rows desc""",
        con,
    )
    print('\n=== DB SOURCE INVENTORY ===')
    print(src.to_string(index=False))

    counts = pd.read_sql_query(
        """select symbol, source, count(*) as rows
           from historical_minute_bars
           where interval_min=1
           group by symbol, source""",
        con,
    )
    counts['symbol'] = counts['symbol'].astype(str).str.strip()
    counts = counts[counts.symbol.map(is_kr_symbol)].copy()
    if counts.empty:
        con.close()
        return {}, src, counts

    # Widest single feed per symbol. Prefer kiwoom_ka10080 on exact row-count ties.
    counts['prefer'] = (counts.source.astype(str) == 'kiwoom_ka10080').astype(int)
    best = (counts.sort_values(['symbol', 'rows', 'prefer'], ascending=[True, False, False])
                  .drop_duplicates('symbol'))

    raw = {}
    chosen_rows = []
    for i, r in enumerate(best.itertuples(index=False), 1):
        sym, source = str(r.symbol), str(r.source)
        q = pd.read_sql_query(
            """select symbol, ts, open, high, low, close, volume
               from historical_minute_bars
               where interval_min=1 and symbol=? and source=?
               order by ts""",
            con,
            params=(sym, source),
        )
        q['time'] = pd.to_datetime(q['ts'], errors='coerce')
        for c in ['open', 'high', 'low', 'close', 'volume']:
            q[c] = pd.to_numeric(q[c], errors='coerce')
        q = q.dropna(subset=['time', 'open', 'high', 'low', 'close'])
        q = q.drop_duplicates('time', keep='last')
        q = q[['time', 'open', 'high', 'low', 'close', 'volume']].sort_values('time').reset_index(drop=True)
        if len(q) < MIN_BARS:
            continue
        raw[pb.n(sym)] = q
        chosen_rows.append(dict(symbol=pb.n(sym), source=source, rows=len(q), min_time=q.time.min(), max_time=q.time.max()))
        if i % 25 == 0 or i == len(best):
            print(f'[LOAD {i}/{len(best)}] usable_symbols={len(raw)}', flush=True)

    con.close()
    chosen = pd.DataFrame(chosen_rows)
    return raw, src, chosen


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 PULLBACK 1M IMPULSE — ALL KR DB ===')
    print('DB=', DB)
    print('Impulse thresholds=', IMPULSE_PCTS)
    print('No pullback-specific 5m ratchet is used.')

    raw, src, chosen = load_all_kr_1m()
    if not raw:
        raise SystemExit('NO USABLE 6-DIGIT KR 1M DATA FOUND')

    print('\n=== CHOSEN KR DATASET ===')
    print('symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()))
    if len(chosen):
        print('date_min=', chosen.min_time.min(), 'date_max=', chosen.max_time.max())
        print('\nsource usage:')
        print(chosen.groupby('source').agg(symbols=('symbol','nunique'), bars=('rows','sum')).sort_values('bars', ascending=False).to_string())

    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    print('\n[BUILD BASELINE — this may take time on a large DB]', flush=True)
    packed, states, tagged, baseline = pb.baseline_objects(raw, cfg)
    bstat = pb.summary('A_BASELINE_ALL_DB', baseline)
    print('\nBASELINE', bstat)

    arms_all = pb.build_arms(raw, cfg, tagged, baseline)
    print('ALL ARMS', len(arms_all), arms_all.arm_reason.value_counts().to_dict() if len(arms_all) else {})

    summaries = [bstat]
    cand_dump = []
    trade_dump = [baseline.assign(case='A_BASELINE_ALL_DB')]

    for gname, reasons in GROUPS.items():
        arms = arms_all[arms_all.arm_reason.isin(reasons)].copy().reset_index(drop=True)
        print(f'\n=== {gname} ===')
        print('arms=', len(arms))

        for threshold in IMPULSE_PCTS:
            q = imp.find_first_impulse_candidates(raw, cfg, arms, threshold)
            if len(q):
                q = q.sort_values(['candidate_time','impulse_pct'], ascending=[True,False]).drop_duplicates(['symbol','candidate_time'])
                qq = q.copy()
                qq['group'] = gname
                cand_dump.append(qq)

            extra_tags = imp.make_extra_tags(q)
            tr = pb.integ.simulate(packed, states, list(tagged) + extra_tags)
            label = f'{gname}_IMP{str(threshold).replace(".","p")}_ALL_DB'
            st = pb.summary(label, tr)
            st['arms'] = len(arms)
            st['selected_candidates'] = len(q)
            st['impulse_threshold_pct'] = threshold
            st['db_symbols'] = len(raw)
            st['db_bars'] = sum(len(x) for x in raw.values())
            summaries.append(st)
            trade_dump.append(tr.assign(case=label))
            print(label, st)

    sdf = pd.DataFrame(summaries)
    print('\n=== SUMMARY ===')
    print(sdf.to_string(index=False))

    sdf.to_csv(OUT / 'summary.csv', index=False)
    src.to_csv(OUT / 'db_source_inventory.csv', index=False)
    chosen.to_csv(OUT / 'chosen_symbol_sources.csv', index=False)
    arms_all.to_csv(OUT / 'arms.csv', index=False)
    if cand_dump:
        pd.concat(cand_dump, ignore_index=True).to_csv(OUT / 'candidates.csv', index=False)
    if trade_dump:
        pd.concat(trade_dump, ignore_index=True).to_csv(OUT / 'trades.csv', index=False)

    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
