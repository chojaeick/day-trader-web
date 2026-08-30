from __future__ import annotations

"""Full US OOS integrated validation using the latest pre-specified KR Slow-turn structure.

This script performs NO OOS tuning. It:
- reuses the US core + fast provisional caches;
- preserves V20 RAW52/REL1.45 and V-rebound RAW30 exactly as frozen from KR;
- replaces only the legacy first-per-day Slow-turn stream with the already-designed
  re-arm + guarded DEEP structure;
- evaluates every KR pre-specified normalized-slope cut (-0.15/-0.20/-0.30/-0.50)
  without selecting a winner from US results;
- writes concise terminal summaries and detailed CSVs.
"""

import pickle
from collections import Counter
from pathlib import Path

import pandas as pd

import tools.build_engine5_us_oos_cache as uscache
import tools.validate_engine5_integrated_full_history as integ
import tools.validate_engine5_integrated_slow_turn_rearm_deep as revised

CACHE_DIR = Path('/home/ubuntu/day-trader-api/engine5_us_oos_cache')
CORE = CACHE_DIR / 'us_engine5_core.pkl'
PERSIST = CACHE_DIR / 'slow_turn_persistence_candidates.csv'
OUT_SUMMARY = CACHE_DIR / 'us_oos_full_integrated_revised_summary.csv'
OUT_TRADES = CACHE_DIR / 'us_oos_full_integrated_revised_trades.csv'
OUT_SIGNALS = CACHE_DIR / 'us_oos_full_integrated_revised_signals.csv'
OUT_SLOW = CACHE_DIR / 'us_oos_full_integrated_revised_slow_candidates.csv'
NY_TZ = 'America/New_York'
CUTS = (-0.15, -0.20, -0.30, -0.50)


def n(x):
    return str(x).zfill(6)


def source_counts(tagged):
    return Counter(x['source'] for x in tagged)


def main():
    if not CORE.exists():
        raise FileNotFoundError(f'{CORE} missing')
    if not PERSIST.exists():
        raise FileNotFoundError(f'{PERSIST} missing')

    with CORE.open('rb') as fh:
        d = pickle.load(fh)
    raw = d['raw']; cfg = d['cfg']; packed = d['packed']; states = d['states']
    scored = d['scored']; strength = d['strength']; completed = d['completed']; micros = d['micros']

    print('=== US OOS FULL INTEGRATED — REVISED SLOW-TURN ===', flush=True)
    print('NO THRESHOLD CHANGES. NO OOS TUNING.', flush=True)
    print('V20 RAW52/REL1.45 + V_REBOUND RAW30 are preserved exactly.', flush=True)

    print('LOAD FAST PROVISIONAL...', flush=True)
    legacy_base, pf_by_symbol = uscache.build_base_candidates_fast(raw, cfg, scored, micros, completed)
    print(f'FAST LEGACY BASE CANDIDATES rows={len(legacy_base)}', flush=True)

    # Patch only data providers so the existing integrated builders consume US fast
    # causal provisional frames instead of the old O(N^2) KR cache builders.
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
        # Build V20 + V-rebound exactly as the existing integrated engine does.
        # Legacy Slow-turn is deliberately removed below.
        current_tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)
        non_slow = [x for x in current_tagged if x['source'] != 'SLOW_TURN']
        legacy_slow = [x for x in current_tagged if x['source'] == 'SLOW_TURN']

        print(f'CURRENT NON-SLOW: V20={sum(x["source"]=="V20" for x in non_slow)} '
              f'V_REBOUND={sum(x["source"]=="V_REBOUND" for x in non_slow)}', flush=True)
        print(f'LEGACY SLOW SIGNALS={len(legacy_slow)}', flush=True)

        print('BUILD ALL RE-ARMED SLOW-TURN CANDIDATES...', flush=True)
        allslow = revised.build_all_slow(raw, cfg, completed, micros)
        if allslow.empty:
            raise SystemExit('NO RE-ARMED SLOW-TURN CANDIDATES')
        print(f'ALL RE-ARMED READY+1M CANDIDATES={len(allslow)}', flush=True)

        rows = []
        trade_parts = []
        signal_parts = []

        # Keep the currently-wired legacy result as a reference only.
        legacy_tr = integ.simulate(packed, states, current_tagged)
        ls = integ.stat('US_LEGACY_WIRING', legacy_tr)
        lc = source_counts(current_tagged)
        rows.append(dict(
            variant='LEGACY_WIRING', cut='LEGACY', slow_signals=lc.get('SLOW_TURN', 0),
            v20_signals=lc.get('V20', 0), v_rebound_signals=lc.get('V_REBOUND', 0), **ls
        ))

        for cut in CUTS:
            sel = revised.select_revised(allslow, cut)
            slow_tags = revised.slow_tags(sel)
            tagged = sorted(non_slow + slow_tags,
                            key=lambda z: (pd.Timestamp(z['time']), z['symbol'], z['source']))
            tr = integ.simulate(packed, states, tagged)
            st = integ.stat(f'US_REVISED_{cut}', tr)
            cnt = source_counts(tagged)
            rows.append(dict(
                variant='REVISED', cut=cut, slow_signals=cnt.get('SLOW_TURN', 0),
                v20_signals=cnt.get('V20', 0), v_rebound_signals=cnt.get('V_REBOUND', 0), **st
            ))

            tq = tr.copy(); tq['cut'] = cut; trade_parts.append(tq)
            sq = pd.DataFrame([{
                'cut': cut, 'source': x['source'], 'symbol': x['symbol'], 'time': x['time']
            } for x in tagged]); signal_parts.append(sq)

        summary = pd.DataFrame(rows)
        summary.to_csv(OUT_SUMMARY, index=False)
        if trade_parts:
            pd.concat(trade_parts, ignore_index=True).to_csv(OUT_TRADES, index=False)
        if signal_parts:
            pd.concat(signal_parts, ignore_index=True).to_csv(OUT_SIGNALS, index=False)
        allslow.drop(columns=['event'], errors='ignore').to_csv(OUT_SLOW, index=False)

        print('\n=== FULL US OOS INTEGRATED SUMMARY ===')
        show = ['variant','cut','slow_signals','v20_signals','v_rebound_signals',
                'trades','wins','losses','win_pct','net_sum_pct','avg_net_pct','pf','max_loss_pct']
        print(summary[show].to_string(index=False, float_format=lambda x: f'{x:.6f}'))

        print('\n=== REVISED CUTS: TRADES BY SOURCE ===')
        alltr = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
        if len(alltr):
            for cut, cg in alltr.groupby('cut'):
                print(f'-- cut {cut} --')
                for src, g in cg.groupby('source'):
                    s = integ.stat(src, g)
                    print(f"{src}: n={s['trades']} win={s['win_pct']:.2f}% net={s['net_sum_pct']:+.6f}% PF={s['pf']:.3f} max_loss={s['max_loss_pct']:+.6f}%")

        print('\n=== REVISED CUTS: EXIT REASONS ===')
        if len(alltr) and 'reason' in alltr.columns:
            for cut, cg in alltr.groupby('cut'):
                print(f'-- cut {cut} --')
                print(cg.reason.value_counts().to_string())

        print('\nIMPORTANT: do not choose/tune a cut from US performance. These four cuts were pre-specified from KR.')
        print('WROTE', OUT_SUMMARY)
        print('WROTE', OUT_TRADES)
        print('WROTE', OUT_SIGNALS)
        print('WROTE', OUT_SLOW)

    finally:
        integ.PERSIST_SRC = old_persist
        integ.sri.reconstruct_base_candidates = old_reconstruct
        integ.vold.load_cache = old_vload
        integ.pd.read_csv = old_read
        revised.st.load_or_build_cache = old_rev_load


if __name__ == '__main__':
    main()
