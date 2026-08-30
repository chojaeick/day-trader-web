from __future__ import annotations

"""Standalone US OOS performance validation for each Engine5 strategy.

No threshold is changed and no US result is used for tuning.
Each source is simulated by itself through the same integrated exit simulator:
- V20: frozen KR RAW52 / REL1.45 + extreme-extension guard.
- Slow-turn: latest re-arm + guarded DEEP structure, evaluated at all four
  pre-specified KR normalized-slope cuts (-0.15/-0.20/-0.30/-0.50).
- V-rebound: frozen KR RAW30 and selected structural state-machine filters.

The script reuses the existing US core and O(N) provisional caches. It does not
rebuild historical minute data or the expensive original provisional cache.
"""

import pickle
from pathlib import Path

import pandas as pd

import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised

CACHE_DIR = Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE = CACHE_DIR / 'us_engine5_core.pkl'
PERSIST = CACHE_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = CACHE_DIR / 'us_oos_each_engine_summary.csv'
OUT_TRADES = CACHE_DIR / 'us_oos_each_engine_trades.csv'
OUT_SYMBOLS = CACHE_DIR / 'us_oos_each_engine_by_symbol.csv'
OUT_EXITS = CACHE_DIR / 'us_oos_each_engine_exit_reasons.csv'
OUT_SIGNALS = CACHE_DIR / 'us_oos_each_engine_signals.csv'
NY_TZ = 'America/New_York'
CUTS = (-0.15, -0.20, -0.30, -0.50)


def n(x):
    return str(x).zfill(6)


def run_variant(label, tagged, packed, states):
    tr = integ.simulate(packed, states, tagged)
    st = integ.stat(label, tr)
    st['signals'] = len(tagged)
    return tr, st


def symbol_rows(label, tr):
    rows = []
    if tr.empty:
        return rows
    for sym, g in tr.groupby('symbol'):
        s = integ.stat(f'{label}:{sym}', g)
        rows.append(dict(variant=label, symbol=sym, **{k: s[k] for k in
            ['trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']}))
    return rows


def exit_rows(label, tr):
    rows = []
    if tr.empty or 'reason' not in tr.columns:
        return rows
    for reason, count in tr.reason.value_counts().items():
        rows.append(dict(variant=label, reason=str(reason), count=int(count)))
    return rows


def main():
    if not CORE.exists():
        raise FileNotFoundError(f'{CORE} missing')
    if not PERSIST.exists():
        raise FileNotFoundError(f'{PERSIST} missing')

    with CORE.open('rb') as fh:
        d = pickle.load(fh)

    raw = d['raw']; cfg = d['cfg']; packed = d['packed']; states = d['states']
    scored = d['scored']; strength = d['strength']; completed = d['completed']; micros = d['micros']

    print('=== US OOS EACH-ENGINE PERFORMANCE VALIDATION ===', flush=True)
    print('NO THRESHOLD CHANGES. NO OOS TUNING.', flush=True)
    print('Reuse existing US core + fast provisional caches.', flush=True)

    legacy_base, pf_by_symbol = uscache.build_base_candidates_fast(raw, cfg, scored, micros, completed)

    old_persist = integ.PERSIST_SRC
    old_reconstruct = integ.sri.reconstruct_base_candidates
    old_vload = integ.vold.load_cache
    old_read = integ.pd.read_csv
    old_rev_load = revised.st.load_or_build_cache

    def fast_reconstruct(*_):
        return legacy_base.copy()

    def fast_vload(sym, *_):
        s = n(sym)
        return pf_by_symbol[s], micros[s]

    def fast_rev_load(sym, *_):
        s = n(sym)
        return pf_by_symbol[s], micros[s]

    def us_read(path, *args, **kwargs):
        df = old_read(path, *args, **kwargs)
        try:
            same = Path(path) == PERSIST
        except TypeError:
            same = False
        if same and 'entry_time' in df.columns:
            df['entry_time'] = pd.to_datetime(df['entry_time'], utc=True).dt.tz_convert(NY_TZ)
        return df

    integ.PERSIST_SRC = PERSIST
    integ.sri.reconstruct_base_candidates = fast_reconstruct
    integ.vold.load_cache = fast_vload
    integ.pd.read_csv = us_read
    revised.st.load_or_build_cache = fast_rev_load

    try:
        # Build current V20 and V-rebound exactly once. The legacy Slow-turn stream
        # is ignored because latest Slow-turn is built separately below.
        current = integ.build_sources(raw, cfg, scored, strength, completed, micros)
        v20 = [x for x in current if x['source'] == 'V20']
        vrebound = [x for x in current if x['source'] == 'V_REBOUND']

        print(f'V20 SIGNALS={len(v20)} | V_REBOUND SIGNALS={len(vrebound)}', flush=True)
        print('BUILD LATEST RE-ARMED SLOW-TURN...', flush=True)
        allslow = revised.build_all_slow(raw, cfg, completed, micros)
        if allslow.empty:
            raise SystemExit('NO RE-ARMED SLOW-TURN CANDIDATES')
        print(f'ALL RE-ARMED READY+1M CANDIDATES={len(allslow)}', flush=True)

        variants = [('V20', v20), ('V_REBOUND', vrebound)]
        for cut in CUTS:
            sel = revised.select_revised(allslow, cut)
            variants.append((f'SLOW_TURN_{cut}', revised.slow_tags(sel)))

        summary_rows = []
        trade_parts = []
        symbol_out = []
        exit_out = []
        signal_out = []

        for label, tagged in variants:
            tr, st = run_variant(label, tagged, packed, states)
            row = dict(variant=label, **{k: st[k] for k in
                ['signals','trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']})
            summary_rows.append(row)

            if len(tr):
                q = tr.copy(); q['variant'] = label; trade_parts.append(q)
            symbol_out.extend(symbol_rows(label, tr))
            exit_out.extend(exit_rows(label, tr))
            signal_out.extend([dict(variant=label, source=x['source'], symbol=x['symbol'], time=x['time']) for x in tagged])

        summary = pd.DataFrame(summary_rows)
        summary.to_csv(OUT_SUMMARY, index=False)
        pd.DataFrame(symbol_out).to_csv(OUT_SYMBOLS, index=False)
        pd.DataFrame(exit_out).to_csv(OUT_EXITS, index=False)
        pd.DataFrame(signal_out).to_csv(OUT_SIGNALS, index=False)
        if trade_parts:
            pd.concat(trade_parts, ignore_index=True).to_csv(OUT_TRADES, index=False)
        else:
            pd.DataFrame().to_csv(OUT_TRADES, index=False)

        print('\n=== EACH ENGINE SUMMARY ===')
        print(summary.to_string(index=False, float_format=lambda x: f'{x:.6f}'))

        print('\n=== BY SYMBOL ===')
        sb = pd.DataFrame(symbol_out)
        if len(sb):
            for variant, g in sb.groupby('variant', sort=False):
                print(f'-- {variant} --')
                for _, r in g.iterrows():
                    print(f"{r.symbol}: n={int(r.trades)} win={r.win_pct:.2f}% net={r.net_sum_pct:+.6f}% PF={r.pf:.3f}")
        else:
            print('NONE')

        print('\n=== EXIT REASONS ===')
        ex = pd.DataFrame(exit_out)
        if len(ex):
            for variant, g in ex.groupby('variant', sort=False):
                print(f'-- {variant} --')
                for _, r in g.iterrows():
                    print(f'{r.reason}: {int(r["count"])}')
        else:
            print('NONE')

        print('\nREADING:')
        print('- V20/V_REBOUND zero trades, if observed, are valid frozen-KR-gate results, not missing simulation.')
        print('- Slow-turn cuts were pre-specified from KR. Do not select or tune one from US performance.')
        print('- These are standalone strategy results; no cross-source position ownership or overlap suppression is applied.')
        print('WROTE', OUT_SUMMARY)
        print('WROTE', OUT_TRADES)
        print('WROTE', OUT_SYMBOLS)
        print('WROTE', OUT_EXITS)
        print('WROTE', OUT_SIGNALS)

    finally:
        integ.PERSIST_SRC = old_persist
        integ.sri.reconstruct_base_candidates = old_reconstruct
        integ.vold.load_cache = old_vload
        integ.pd.read_csv = old_read
        revised.st.load_or_build_cache = old_rev_load


if __name__ == '__main__':
    main()
