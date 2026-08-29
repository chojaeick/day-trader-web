from __future__ import annotations

from dataclasses import replace
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_breakout_first10_hwm1pct as v17c
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OPEN_MINUTE=9*60+10
THRESHOLD=50
TARGETS=[
    ('GOOD_950160_0915','950160',pd.Timestamp('2026-08-13 09:15:00+09:00')),
    ('GOOD_950160_0920','950160',pd.Timestamp('2026-08-13 09:20:00+09:00')),
    ('GOOD_950160_0930','950160',pd.Timestamp('2026-08-13 09:30:00+09:00')),
    ('FAIL_257720_1420','257720',pd.Timestamp('2026-08-18 14:20:00+09:00')),
    ('FAIL_257720_1430','257720',pd.Timestamp('2026-08-18 14:30:00+09:00')),
]

def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}

def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in frames.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored)); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17b,added,skipped=v17b.build_v17b(ev16,scored,waits)
    trades=v17c.simulate_unconditional_hwm(packed,ev17b,states,THRESHOLD)
    print('=== V17C PATH OCCUPANCY DIAGNOSTIC ===')
    print('No strategy change. Shows whether a valid candidate was skipped because the single-position simulator was already occupied.')
    print('\n=== TARGETS ===')
    for label,sym,ts in TARGETS:
        cands=[e for e in ev17b.get(ts,[]) if str(e[0]).zfill(6)==sym]
        realized=trades[(trades.symbol.astype(str).str.zfill(6)==sym)&(pd.to_datetime(trades.entry_time)==ts)]
        occupied=trades[(pd.to_datetime(trades.entry_time)<=ts)&(pd.to_datetime(trades.exit_time)>ts)]
        print(f'\n[{label}] {sym} {ts}')
        print('candidate_present=',bool(cands),'candidate_count=',len(cands),'realized=',not realized.empty)
        if cands: print('candidate=',cands[0])
        if not occupied.empty:
            print('OCCUPIED_BY:')
            print(occupied[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','reason','breakout_entry']].to_string(index=False))
        else:
            print('OCCUPIED_BY: none')
        if not realized.empty:
            print('REALIZED_TRADE:')
            print(realized[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','reason','breakout_entry']].to_string(index=False))
    print('\n=== 950160 2026-08-13 TRADES ===')
    q=trades[(trades.symbol.astype(str).str.zfill(6)=='950160')&(pd.to_datetime(trades.entry_time).dt.date==pd.Timestamp('2026-08-13').date())]
    print(q.to_string(index=False) if len(q) else 'none')
    print('\n=== 257720 2026-08-18 TRADES ===')
    q=trades[(trades.symbol.astype(str).str.zfill(6)=='257720')&(pd.to_datetime(trades.entry_time).dt.date==pd.Timestamp('2026-08-18').date())]
    print(q.to_string(index=False) if len(q) else 'none')
    print('\nBREAKOUT_ADDED=',added)
    print('BREAKOUT_SKIPPED=',skipped)

if __name__=='__main__': main()
