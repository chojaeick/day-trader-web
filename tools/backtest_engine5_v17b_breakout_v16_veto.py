from __future__ import annotations

from dataclasses import replace
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17_volume_bypass_tight10 as v17
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD=50
OPEN_MINUTE=9*60+10
TARGET_SUCCESS=('257720',pd.Timestamp('2026-08-12 09:10:00+09:00'))
TARGET_EARLY=('257720',pd.Timestamp('2026-08-18 14:20:00+09:00'))
TARGET_VETO=('484810',pd.Timestamp('2026-08-11 09:10:00+09:00'))


def filt_open(ev):
    return {ts:rows for ts,rows in ev.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=OPEN_MINUTE}


def wait_keys(waits:pd.DataFrame)->set[tuple[str,pd.Timestamp]]:
    if waits is None or waits.empty:return set()
    return {(str(r.symbol).zfill(6),pd.Timestamp(r.signal_time)) for r in waits.itertuples(index=False)}


def build_v17b(ev16,frames,waits):
    out={ts:[tuple(list(e)+[False]) for e in rows] for ts,rows in ev16.items()}
    veto=wait_keys(waits)
    added=[]; skipped=[]
    for sym,f0 in frames.items():
        f=v17.enrich_for_v17(f0)
        for r in f[f['breakout_candidate']].itertuples(index=False):
            ts=pd.Timestamp(r.time); key=(str(sym).zfill(6),ts)
            if ts.hour*60+ts.minute<OPEN_MINUTE:continue
            if float(r.entry_score)<THRESHOLD:continue
            if key in veto:
                skipped.append((key[0],ts,float(r.close),float(r.volume_ratio_prev),'V16_WAIT_VETO'))
                continue
            e=v17.tuple_from_row(key[0],r,True)
            if e is None:continue
            already=any(str(x[0]).zfill(6)==key[0] for x in out.get(ts,[]))
            if not already:
                out.setdefault(ts,[]).append(e)
                added.append((key[0],ts,float(r.close),float(r.volume_ratio_prev)))
    return out,added,skipped


def trade_present(t,sym,ts):
    if t.empty:return False
    q=t[(t.symbol.astype(str).str.zfill(6)==sym)&(pd.to_datetime(t.entry_time)==ts)]
    return not q.empty


def main():
    raw=load_data(); base_cfg=DoubleBollingerEngine5Config(); cfg=replace(base_cfg,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed_exits=v8.base.pack_exit_events(raw,base_cfg)
    state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    raw_frames=base.build_cfg_frames(raw,cfg)
    f10={s:v10._refine_entry_frame(f) for s,f in raw_frames.items()}
    scored=reweight(f10,cfg,0.0)
    ev10=filt_open(v8.pack_entry_events(scored))
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False)
    ev16x={ts:[tuple(list(e)+[False]) for e in rows] for ts,rows in ev16.items()}
    ev17b,added,skipped=build_v17b(ev16,scored,waits)

    print('=== V17B ISOLATED BREAKOUT TEST ===')
    print('Changes ONLY: >=10x 5m volume + live MACD/spread/RSI acceleration may bypass trend persistence. Any V16 WAIT signal is a hard veto. No broad deceleration filter.')
    t16,s16=v17.run('A_V16_SAME_SIM',packed_exits,state_events,ev16x)
    t17,s17=v17.run('B_V17B_BREAKOUT_VETO',packed_exits,state_events,ev17b)
    print('\nV16_WAIT_SIGNALS')
    print(waits.to_string(index=False) if len(waits) else 'none')
    print('\nBREAKOUT_ADDED=',added)
    print('BREAKOUT_SKIPPED_BY_V16_WAIT=',skipped)
    print('\nBREAKOUT REALIZED TRADES')
    q=t17[t17.breakout_entry==True]
    print(q.to_string(index=False) if len(q) else 'none')
    print('\nREGRESSION CHECKS')
    print('257720_2026-08-12_0910_success_preserved=',trade_present(t17,*TARGET_SUCCESS))
    print('257720_2026-08-18_1420_breakout_candidate_added=',any(a[0]==TARGET_EARLY[0] and a[1]==TARGET_EARLY[1] for a in added))
    print('257720_2026-08-18_1420_realized=',trade_present(t17,*TARGET_EARLY))
    print('484810_2026-08-11_0910_vetoed=',any(a[0]==TARGET_VETO[0] and a[1]==TARGET_VETO[1] for a in skipped))
    print('484810_2026-08-11_0910_realized=',trade_present(t17,*TARGET_VETO))
    print('\nDELTA win={:+.2f} gross={:+.4f} pf={:+.3f}'.format(s17['win_rate']-s16['win_rate'],s17['gross_pct']-s16['gross_pct'],s17['pf']-s16['pf']))
    out='/home/ubuntu/day-trader-api/engine5_v16_full_validation/v17b_breakout_v16_veto_trades.csv'
    t17.to_csv(out,index=False); print('[CSV]',out)

if __name__=='__main__':main()
