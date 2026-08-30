from __future__ import annotations

"""Compare fresh V21E source combinations using the already-built fresh map.

No DB remap is performed. The script reloads the exact fresh SQLite-derived map,
rebuilds only exit/state event packs from saved raw bars, then runs the same integrated
V21 simulator for several source combinations so position ownership/conflicts are respected.
"""

import pickle
from pathlib import Path

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.validate_engine5_integrated_full_history as integ
import tools.remap_and_validate_engine5_v21e_fresh_from_us_db as fresh
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config

ROOT = Path('/home/ubuntu/day-trader-api/engine5_v21e_fresh_validation')
MAP_PKL = ROOT / 'v21e_fresh_map.pkl'
OUT = ROOT / 'v21e_fresh_source_combinations.csv'

COMBOS = [
    ('V_REBOUND_ONLY', {'V_REBOUND_E'}),
    ('V20_ONLY', {'V20E'}),
    ('SLOW_ONLY', {'SLOW_TURN_E'}),
    ('V20_PLUS_V_REBOUND', {'V20E', 'V_REBOUND_E'}),
    ('V20_PLUS_SLOW', {'V20E', 'SLOW_TURN_E'}),
    ('V_REBOUND_PLUS_SLOW', {'V_REBOUND_E', 'SLOW_TURN_E'}),
    ('FULL_V21E', {'V20E', 'SLOW_TURN_E', 'V_REBOUND_E'}),
]


def main():
    if not MAP_PKL.exists():
        raise FileNotFoundError(MAP_PKL)
    with MAP_PKL.open('rb') as fh:
        d = pickle.load(fh)

    if d.get('schema') != 'V21E_FRESH_SQLITE_USD_ET_V1':
        raise RuntimeError(f"unexpected schema: {d.get('schema')}")

    raw = d['raw']
    tags = d['tags']
    cfg0 = DoubleBollingerEngine5Config()

    print('=== V21E FRESH SOURCE-COMBINATION SIMULATION ===')
    print('Uses saved fresh SQLite/USD/ET map; NO DB REMAP.')
    print('Re-simulates each combination so source conflicts / position ownership are respected.\n')

    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))

    rows = []
    for label, allowed in COMBOS:
        subset = [x for x in tags if x['source'] in allowed]
        tr = integ.simulate(packed, states, subset)
        m = fresh.metrics(tr)
        rows.append(dict(
            combo=label,
            signals=len(subset),
            sources='+'.join(sorted(allowed)),
            **m,
        ))

    out = pd.DataFrame(rows)
    show = [
        'combo','signals','trades','gross_win_pct','gross_sum_pct','gross_pf',
        'net025_win_pct','net025_sum_pct','net025_pf','max_net025_loss_pct'
    ]
    print(out[show].to_string(index=False, float_format=lambda x: f'{x:.4f}'))
    out.to_csv(OUT, index=False)
    print('\nWROTE', OUT)


if __name__ == '__main__':
    main()
