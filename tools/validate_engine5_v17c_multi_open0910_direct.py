from __future__ import annotations

from dataclasses import replace
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OPEN_MINUTE=9*60+10
THRESHOLD=50
TARGET='058610'
TARGET_TS=pd.Timestamp('2026-08-11 09:10:00+09:00')


def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,base_cfg); states=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    frames=base.build_cfg_frames(raw,cfg); f10={s:v10._refine_entry_frame(f) for s,f in frames.items()}; scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored))
    # Intended rule: 09:00-09:09 no BUY. From 09:10 onward, a valid 5m signal is directly tradable.
    # Do not apply inherited V15/V16 09:10-09:59 opening-slope WAIT.
    empty_waits=pd.DataFrame()
    ev_direct,added,skipped=v17b.build_v17b(ev10,scored,empty_waits)

    # Current inherited V17C path for comparison.
    import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev_current,_,_=v17b.build_v17b(ev16,scored,waits)

    t_current=multi.simulate_multi(packed,ev_current,states,THRESHOLD)
    t_direct=multi.simulate_multi(packed,ev_direct,states,THRESHOLD)

    print('=== V17C MULTI: CURRENT OPENING-WAIT VS 09:10 DIRECT ===')
    print('Intended session rule under test: 09:00-09:09 no BUY; 09:10+ valid signal may enter immediately.')
    multi.metrics('A_CURRENT_INHERITED_V16_WAIT',t_current)
    multi.metrics('B_DIRECT_FROM_0910',t_direct)

    print('\n=== TARGET 058610 2026-08-11 09:10 ===')
    for label,t in [('CURRENT',t_current),('DIRECT',t_direct)]:
        q=t[(t.symbol.astype(str).str.zfill(6)==TARGET)&(pd.to_datetime(t.entry_time)==TARGET_TS)]
        print(label,'PRESENT=',not q.empty)
        if len(q): print(q.to_string(index=False))

    print('\n=== CURRENT V16 WAIT RECORD FOR TARGET ===')
    if waits is not None and len(waits):
        q=waits[(waits.symbol.astype(str).str.zfill(6)==TARGET)&(pd.to_datetime(waits.signal_time)==TARGET_TS)]
        print(q.to_string(index=False) if len(q) else 'none')
    else: print('none')

    print('\nBREAKOUT_ADDED_DIRECT=',added)
    print('BREAKOUT_SKIPPED_DIRECT=',skipped)
    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v17c_multi_open0910_direct.csv'; t_direct.to_csv(out,index=False); print('[CSV]',out)

if __name__=='__main__': main()
