from __future__ import annotations

from pathlib import Path

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8

# V9 adds an opening-no-entry window requested after manual chart review:
# 09:00:00~09:09:59 KST entries are blocked; first allowed entry is 09:10.
# All V8 entry scoring/gates, strict 1R stop, TP1, and V7 exit state machine remain unchanged.
base.CHECKPOINT = Path('/home/ubuntu/day-trader-api/dbb_engine5_exit_v9_checkpoint.csv')
OPEN_ENTRY_MINUTE = 9 * 60 + 10


def pack_entry_events(scored_frames):
    ev = v8.pack_entry_events(scored_frames)
    filtered = {}
    for ts, rows in ev.items():
        t = pd.Timestamp(ts)
        minute = t.hour * 60 + t.minute
        if minute >= OPEN_ENTRY_MINUTE:
            filtered[ts] = rows
    return filtered


base.pack_exit_events = v8.base.pack_exit_events
base.pack_entry_events = pack_entry_events
base.simulate_v4 = v8.v7.simulate_v7


def main():
    print('[ENGINE5 EXIT V9] V8 strict 1R/2R logic retained; NEW ENTRY RULE: no new entries from 09:00 through 09:09 KST; first allowed entry 09:10.', flush=True)
    base.main()


if __name__ == '__main__':
    main()
