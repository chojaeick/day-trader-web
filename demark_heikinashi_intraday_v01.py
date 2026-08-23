#!/usr/bin/env python3
"""TOM DEMARK + HEIKIN-ASHI INTRADAY v0.1

Baseline hypothesis test (not production logic).
- Source: REGULAR 1m historical_minute_bars, aggregated causally to 5m.
- Long-only baseline.
- TD-style Buy Setup exhaustion: 9 consecutive 5m closes below close 4 bars earlier.
- After setup completes, wait up to 6 bars for Heikin-Ashi bullish reversal.
- HA bullish reversal: current HA close > HA open and previous HA close <= previous HA open.
- Exit: opposite HA flip, hard stop -0.8%, max hold 18 bars, or EOD.
- Cost sweep: 0.20 / 0.25 / 0.30% round trip.
- DB read only. No auto order.

This deliberately uses a simple, causal approximation of a DeMark exhaustion concept plus
Heikin-Ashi reversal confirmation. If the baseline has edge, later versions can audit stricter
TD Sequential details and alternative HA confirmation rules on a fresh split.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DB = ROOT / "daytrader.db"
MIN_DAYS = 100
COSTS = [0.20, 0.25, 0.30]
TD_N = 9
ARM_BARS = 6
HARD_STOP = 0.80
MAX_HOLD = 18


def discover_symbols():
    con = sqlite3.connect(DB)
    q = """
    SELECT symbol, COUNT(DISTINCT trade_date) AS days
    FROM historical_minute_bars
    WHERE interval_min=1 AND session='REGULAR'
    GROUP BY symbol
    HAVING days >= ?
    ORDER BY symbol
    """
    z = pd.read_sql_query(q, con, params=[MIN_DAYS])
    con.close()
    return z.symbol.astype(str).tolist()


def load_5m(symbols):
    if not symbols:
        return {}
    con = sqlite3.connect(DB)
    marks = ','.join('?' for _ in symbols)
    q = f"""
    SELECT symbol,trade_date,et_time,open,high,low,close,volume
    FROM historical_minute_bars
    WHERE interval_min=1 AND session='REGULAR' AND symbol IN ({marks})
    ORDER BY symbol,trade_date,et_time
    """
    x = pd.read_sql_query(q, con, params=symbols)
    con.close()
    out = {}
    for (s,d), z in x.groupby(['symbol','trade_date'], sort=True):
        if len(z) < 60:
            continue
        z = z.copy().reset_index(drop=True)
        z['bucket'] = np.arange(len(z)) // 5
        b = (z.groupby('bucket', sort=True)
              .agg(time=('et_time','last'), open=('open','first'), high=('high','max'),
                   low=('low','min'), close=('close','last'), volume=('volume','sum'))
              .reset_index(drop=True))
        if len(b) >= 20:
            out[(str(s), str(d))] = b
    return out


def add_ha_td(b):
    z = b.copy()
    o = z.open.to_numpy(float); h = z.high.to_numpy(float)
    l = z.low.to_numpy(float); c = z.close.to_numpy(float)
    n = len(z)

    ha_c = (o+h+l+c)/4.0
    ha_o = np.empty(n, float)
    ha_o[0] = (o[0]+c[0])/2.0
    for i in range(1,n):
        ha_o[i] = (ha_o[i-1]+ha_c[i-1])/2.0
    z['ha_open'] = ha_o
    z['ha_close'] = ha_c
    z['ha_bull'] = z.ha_close > z.ha_open
    z['ha_bear'] = z.ha_close < z.ha_open
    z['ha_bull_flip'] = z.ha_bull & (~z.ha_bull.shift(1).fillna(False))
    z['ha_bear_flip'] = z.ha_bear & (~z.ha_bear.shift(1).fillna(False))

    cond = np.zeros(n, dtype=bool)
    if n > 4:
        cond[4:] = c[4:] < c[:-4]
    td = np.zeros(n, dtype=int)
    k = 0
    for i in range(n):
        if cond[i]:
            k += 1
        else:
            k = 0
        td[i] = k
    z['td_buy_count'] = td
    z['td9'] = z.td_buy_count == TD_N
    return z


def simulate_day(sym, day, z):
    trades = []
    armed_until = -1
    i = 0
    n = len(z)
    while i < n:
        if bool(z.iloc[i].td9):
            armed_until = max(armed_until, i + ARM_BARS)

        if i <= armed_until and i > 0 and bool(z.iloc[i].ha_bull_flip):
            entry = float(z.iloc[i].close)
            et = str(z.iloc[i].time)
            end = min(n-1, i + MAX_HOLD)
            reason = 'TIME'
            exit_px = float(z.iloc[end].close)
            exit_i = end
            for j in range(i+1, end+1):
                low_ret = (float(z.iloc[j].low)/entry - 1.0)*100.0
                if low_ret <= -HARD_STOP:
                    exit_px = entry*(1.0-HARD_STOP/100.0)
                    exit_i = j; reason='STOP'; break
                if bool(z.iloc[j].ha_bear_flip):
                    exit_px = float(z.iloc[j].close)
                    exit_i = j; reason='HA_FLIP'; break
            if exit_i == n-1 and reason == 'TIME':
                reason = 'EOD'
            gross = (exit_px/entry - 1.0)*100.0
            trades.append((sym,day,et,str(z.iloc[exit_i].time),entry,exit_px,gross,reason))
            armed_until = -1
            i = exit_i + 1
        else:
            i += 1
    return trades


def summarize(trades, cost):
    if not trades:
        return dict(TRADES=0,NET=0.,AVG=0.,WIN_RATE=0.,PF=0.,WORST=0.,POS_DATES=0,DATES=0,STOP_RATE=0.)
    x = pd.DataFrame(trades, columns=['symbol','date','entry_time','exit_time','entry','exit','gross','reason'])
    x['net'] = x.gross - cost
    pos = x.loc[x.net>0,'net'].sum(); neg = -x.loc[x.net<0,'net'].sum()
    bydate = x.groupby('date').net.sum()
    return dict(TRADES=len(x), NET=float(x.net.sum()), AVG=float(x.net.mean()),
                WIN_RATE=float((x.net>0).mean()*100), PF=(float(pos/neg) if neg>0 else float('inf')),
                WORST=float(x.net.min()), POS_DATES=int((bydate>0).sum()), DATES=len(bydate),
                STOP_RATE=float((x.reason=='STOP').mean()*100))


def main():
    syms = discover_symbols()
    data = load_5m(syms)
    all_trades = []
    td9_events = 0; ha_confirms = 0
    for (s,d), b in data.items():
        z = add_ha_td(b)
        td9_events += int(z.td9.sum())
        # diagnostic: bullish flips within the next ARM_BARS after a TD9
        td_idx = np.flatnonzero(z.td9.to_numpy(bool))
        bull_idx = np.flatnonzero(z.ha_bull_flip.to_numpy(bool))
        for q in td_idx:
            if np.any((bull_idx>=q) & (bull_idx<=q+ARM_BARS)):
                ha_confirms += 1
        all_trades.extend(simulate_day(s,d,z))

    print('===== TOM DEMARK + HEIKIN-ASHI INTRADAY v0.1 =====')
    print('5M FROM REGULAR 1M / LONG ONLY / DB READ ONLY / NO AUTO ORDER')
    print('SYMBOLS',len(syms),','.join(syms))
    print('SYMBOL_DAYS',len(data),'TD9_EVENTS',td9_events,'TD9_WITH_HA_CONFIRM',ha_confirms)
    print('PARAMS TD_N',TD_N,'ARM_BARS',ARM_BARS,'HARD_STOP',HARD_STOP,'MAX_HOLD',MAX_HOLD)
    print('\n===== COST SWEEP =====')
    rows=[]
    for cost in COSTS:
        m=summarize(all_trades,cost); rows.append((cost,*m.values()))
    cols=['COST','TRADES','NET','AVG','WIN_RATE','PF','WORST','POS_DATES','DATES','STOP_RATE']
    r=pd.DataFrame(rows,columns=cols)
    print(r.round(3).to_string(index=False))
    if all_trades:
        x=pd.DataFrame(all_trades,columns=['symbol','date','entry_time','exit_time','entry','exit','gross','reason'])
        print('\n===== BY SYMBOL @ COST0.20 =====')
        x['net']=x.gross-0.20
        q=x.groupby('symbol').agg(TRADES=('net','size'),NET=('net','sum'),AVG=('net','mean'),WIN_RATE=('net',lambda s:(s>0).mean()*100)).sort_values('NET',ascending=False)
        print(q.round(3).to_string())
        print('\n===== EXIT REASONS =====')
        print(x.reason.value_counts().to_string())
    base=summarize(all_trades,0.20); c30=summarize(all_trades,0.30)
    print('\n===== DECISION SUPPORT =====')
    print('SAMPLE_OK',base['TRADES']>=30)
    print('BASE_COST_POSITIVE',base['NET']>0 and base['PF']>1)
    print('COST30_POSITIVE',c30['NET']>0 and c30['PF']>1)
    print('WIN_RATE_GE_60',base['WIN_RATE']>=60)
    print('NEXT: if baseline is weak, audit TD exhaustion quality versus HA confirmation before tuning numeric thresholds.')

if __name__=='__main__':
    main()
