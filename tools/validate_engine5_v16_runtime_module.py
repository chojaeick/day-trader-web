from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from live_server.double_bollinger_engine5_v16 import DoubleBollingerEngine5V16
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary

OPEN_MINUTE = 9 * 60 + 10
TARGET = pd.Timestamp('2026-08-11 09:10:00+09:00')
TARGET_SYMBOL = '484810'

PANEL = [
    ('S_M2.0_R1.5', 50, 2.0, 1.5, None),
    ('S_M1.5_R1.0', 55, 1.5, 1.0, None),
    ('S_M1.0_R1.0', 55, 1.0, 1.0, None),
    ('S_M1.0_R1.5', 50, 1.0, 1.5, None),
    ('C_A10_V10_O10', 55, 2.0, 2.0, {'w_rsi_accel':10.0,'w_volume':10.0,'w_outer_expand':10.0}),
    ('C_A10_V10_O10', 50, 2.0, 2.0, {'w_rsi_accel':10.0,'w_volume':10.0,'w_outer_expand':10.0}),
    ('C_A5_V10_O10', 50, 2.0, 2.0, {'w_rsi_accel':5.0,'w_volume':10.0,'w_outer_expand':10.0}),
    ('W_M30_R30', 55, 2.0, 2.0, {'w_macd_gap':30.0,'w_rsi_state':30.0}),
    ('S_M3.0_R1.0', 65, 3.0, 1.0, None),
    ('S_M3.0_R3.0', 70, 3.0, 3.0, None),
]


def cfg_for(mr, rr, extras):
    cfg = replace(DoubleBollingerEngine5Config(), macd_slope_spread_full_ratio=mr, rsi_slope_full_ratio=rr)
    if extras:
        cfg = replace(cfg, **extras)
    return cfg


def filter_open(ev):
    return {ts: rows for ts, rows in ev.items() if pd.Timestamp(ts).hour * 60 + pd.Timestamp(ts).minute >= OPEN_MINUTE}


def daily_gap_map(raw_bars: pd.DataFrame) -> dict:
    d = raw_bars.copy().sort_values('time')
    d['time'] = pd.to_datetime(d['time'])
    d['date'] = d['time'].dt.date
    days=[]
    for day,g in d.groupby('date',sort=True):
        days.append((day,float(g.iloc[0]['open']),float(g.iloc[-1]['close'])))
    out={}
    for i,(day,op,_) in enumerate(days):
        if i==0:
            out[day]=np.nan
        else:
            pc=days[i-1][2]
            out[day]=(op/pc-1.0)*100.0 if pc else np.nan
    return out


def delayed_event(original_event, price):
    e=list(original_event)
    e[1]=float(price)
    return tuple(e)


def build_runtime_events(ev10, raw, engine):
    out={ts:list(rows) for ts,rows in ev10.items()}
    gaps={sym:daily_gap_map(raw[sym]) for sym in raw}
    waits=[]
    for ts in sorted(ev10):
        t=pd.Timestamp(ts)
        for event in list(ev10[ts]):
            sym=str(event[0]).zfill(6)
            gap=gaps[sym].get(t.date(),np.nan)
            wait,state=engine.should_wait_opening_signal(raw[sym],t,gap)
            if not wait:
                continue
            out[ts]=[x for x in out.get(ts,[]) if x!=event]
            if not out[ts]:
                out.pop(ts,None)
            re=engine.first_reaccel(raw[sym],t)
            rec={'symbol':sym,'signal_time':t,'signal_price':float(event[1]),'gap_pct':gap,
                 'down_steps':state['down_steps'],'fade_ratio':state['fade_ratio'],'step_ratio':state['step_ratio'],
                 'status':'NO_REACCEL','delayed_time':pd.NaT,'delayed_price':np.nan}
            if re is not None:
                out.setdefault(re['time'],[]).append(delayed_event(event,re['price']))
                rec.update({'status':'REACCEL_ENTRY','delayed_time':re['time'],'delayed_price':re['price']})
            waits.append(rec)
    return out,pd.DataFrame(waits)


def run(name, packed_exits, state_events, events, th):
    t,c=v8.v7.simulate_v7(packed_exits,events,state_events,th)
    s=summary(name,t)
    return t,c,s


def main():
    raw=load_data()
    base_cfg=DoubleBollingerEngine5Config()
    packed_exits=v8.base.pack_exit_events(raw,base_cfg)
    state_events=base.pack_state_events(base.build_cfg_frames(raw,base_cfg))
    rows=[]
    target_rows=[]
    print('=== ENGINE5 V16 RUNTIME MODULE REGRESSION ===')
    for name,th,mr,rr,extras in PANEL:
        cfg=cfg_for(mr,rr,extras)
        engine=DoubleBollingerEngine5V16(cfg)
        frames=base.build_cfg_frames(raw,cfg)
        refined={sym:engine.refine_v10_entry_frame(f) for sym,f in frames.items()}
        scored=reweight(refined,cfg,0.0)
        ev10=filter_open(v8.pack_entry_events(scored))
        ev16,waits=build_runtime_events(ev10,raw,engine)
        t10,c10,s10=run('V10',packed_exits,state_events,ev10,th)
        t16,c16,s16=run('V16_RUNTIME',packed_exits,state_events,ev16,th)
        dwin=float(s16['win_rate'])-float(s10['win_rate'])
        dgross=float(s16['gross_pct'])-float(s10['gross_pct'])
        dpf=float(s16['pf'])-float(s10['pf'])
        print(f'{name:18s} th={th:2d} | V10 {len(t10):3d}t win={s10["win_rate"]:.2f} gross={s10["gross_pct"]:+.4f} pf={s10["pf"]:.3f} | V16 {len(t16):3d}t win={s16["win_rate"]:.2f} gross={s16["gross_pct"]:+.4f} pf={s16["pf"]:.3f} | DELTA win={dwin:+.2f} gross={dgross:+.4f} pf={dpf:+.3f}')
        rows.append({'config':name,'th':th,'d_win':dwin,'d_gross':dgross,'d_pf':dpf})
        q=waits[(waits.symbol==TARGET_SYMBOL)&(pd.to_datetime(waits.signal_time)==TARGET)] if len(waits) else waits
        target_rows.append({'config':name,'th':th,'target_status':q.iloc[0]['status'] if len(q) else 'NOT_WAIT_CANDIDATE'})

    board=pd.DataFrame(rows)
    print('\n=== RUNTIME MODULE ROBUSTNESS SUMMARY ===')
    print('configs=',len(board))
    print('win_improved=',int((board.d_win>0).sum()),'worse=',int((board.d_win<0).sum()))
    print('gross_improved=',int((board.d_gross>0).sum()),'worse=',int((board.d_gross<0).sum()))
    print('pf_improved=',int((board.d_pf>0).sum()),'worse=',int((board.d_pf<0).sum()))
    print('median_d_win=',round(float(board.d_win.median()),4))
    print('median_d_gross=',round(float(board.d_gross.median()),4))
    print('median_d_pf=',round(float(board.d_pf.median()),4))
    print('\n=== TARGET 484810 ===')
    print(pd.DataFrame(target_rows).to_string(index=False))
    ok=bool((board.d_win>0).all() and (board.d_gross>0).all() and (board.d_pf>0).all() and all(x['target_status']=='NO_REACCEL' for x in target_rows))
    print('\nRUNTIME_MODULE_REGRESSION_PASS=',ok)


if __name__=='__main__':
    main()
