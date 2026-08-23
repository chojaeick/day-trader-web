#!/usr/bin/env python3
"""Fujimoto staged baseline v0.1

Goal: find the strongest causal component before integrating into V5.
Uses existing historical_minute_bars only. No downloader/backfill.

Stages
F0: bullish RSI divergence
F1: F0 + RSI recovery confirmation
F2: F1 + second confirmation / momentum continuation
F3: F2 + Ichimoku cloud breakout + chikou alignment
F4: F3 + staged exit proxy (10/20/70 inspired) with fixed causal rules

This is a tested reimplementation baseline, not an original-strategy reproduction.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DB_DEFAULT = Path('/home/ubuntu/day-trader-api/daytrader.db')
COSTS = [0.0, 0.20, 0.25, 0.30]  # round-trip percent points


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    high, low, close = df['high'], df['low'], df['close']
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    chikou = close.shift(-26)
    return pd.DataFrame({'tenkan': tenkan, 'kijun': kijun, 'span_a': span_a, 'span_b': span_b, 'chikou': chikou})


def load_bars(con: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    q = """
    SELECT et_time, open, high, low, close, volume
    FROM historical_minute_bars
    WHERE symbol=? AND interval_min=1
    ORDER BY ts
    """
    df = pd.read_sql_query(q, con, params=(symbol,))
    if df.empty:
        return df
    # The DB spans EST/EDT and therefore contains mixed UTC offsets. Parse in UTC
    # first so pandas always creates a real DatetimeIndex, then convert to New York.
    df['et_time'] = pd.to_datetime(df['et_time'], errors='coerce', utc=True)
    df = df.dropna(subset=['et_time']).set_index('et_time')
    df.index = df.index.tz_convert('America/New_York')
    df = df[~df.index.duplicated(keep='last')].sort_index()
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna(subset=['open','high','low','close'])


def resample5(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError(f'expected DatetimeIndex, got {type(df.index).__name__}')
    x = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    return x


def pivots(s: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    out = pd.Series(False, index=s.index)
    vals = s.values
    for i in range(left, len(s)-right):
        v = vals[i]
        if np.isfinite(v) and v <= np.nanmin(vals[i-left:i+right+1]):
            out.iloc[i] = True
    return out


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x['rsi'] = rsi(x['close'], 14)
    x['ema8'] = x['close'].ewm(span=8, adjust=False).mean()
    x['ema20'] = x['close'].ewm(span=20, adjust=False).mean()
    x['atr'] = pd.concat([
        x['high']-x['low'],
        (x['high']-x['close'].shift()).abs(),
        (x['low']-x['close'].shift()).abs()
    ], axis=1).max(axis=1).rolling(14).mean()
    x = x.join(ichimoku(x))
    x['pivot_low'] = pivots(x['low'])

    div = pd.Series(False, index=x.index)
    piv_idx = [i for i,b in enumerate(x['pivot_low'].values) if b]
    for j in range(1, len(piv_idx)):
        i0, i1 = piv_idx[j-1], piv_idx[j]
        if i1-i0 > 40:
            continue
        p0, p1 = x['low'].iloc[i0], x['low'].iloc[i1]
        r0, r1 = x['rsi'].iloc[i0], x['rsi'].iloc[i1]
        if p1 < p0 and r1 > r0 and r0 < 45 and r1 < 50:
            sig_i = min(i1+2, len(x)-1)
            div.iloc[sig_i] = True
    x['F0'] = div
    recent_div = x['F0'].rolling(7, min_periods=1).max().astype(bool)
    x['F1'] = recent_div & (x['rsi'] > 40) & (x['rsi'] > x['rsi'].shift(1)) & (x['close'] > x['close'].shift(1))
    recent_f1 = x['F1'].rolling(4, min_periods=1).max().astype(bool)
    x['F2'] = recent_f1 & (x['rsi'] > 48) & (x['ema8'] > x['ema20']) & (x['close'] > x['ema8'])
    cloud_top = pd.concat([x['span_a'], x['span_b']], axis=1).max(axis=1)
    cloud_prev = cloud_top.shift(1)
    recent_f2 = x['F2'].rolling(6, min_periods=1).max().astype(bool)
    chikou_align = x['close'] > x['close'].shift(26)
    x['F3'] = recent_f2 & (x['close'] > cloud_top) & (x['close'].shift(1) <= cloud_prev) & chikou_align
    return x


@dataclass
class Trade:
    symbol: str
    stage: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry: float
    exit: float
    gross_pct: float
    mfe_pct: float
    mae_pct: float


def run_stage(symbol: str, x: pd.DataFrame, stage: str) -> list[Trade]:
    sig = x[stage].fillna(False).values
    trades: list[Trade] = []
    i = 60
    while i < len(x)-2:
        if not sig[i]:
            i += 1
            continue
        entry_i = i+1
        entry = float(x['open'].iloc[entry_i])
        if not math.isfinite(entry) or entry <= 0:
            i += 1
            continue
        end_i = min(entry_i+24, len(x)-1)
        exit_i = end_i
        if stage == 'F4':
            for k in range(entry_i+1, end_i+1):
                r = x['rsi'].iloc[k]
                if (r < 50 and x['rsi'].iloc[k-1] >= 50) or x['close'].iloc[k] < x['ema20'].iloc[k]:
                    exit_i = k
                    break
        else:
            for k in range(entry_i+3, end_i+1):
                if x['close'].iloc[k] < x['ema8'].iloc[k]:
                    exit_i = k
                    break
        exit_px = float(x['close'].iloc[exit_i])
        window = x.iloc[entry_i:exit_i+1]
        gross = (exit_px/entry - 1)*100
        mfe = (window['high'].max()/entry - 1)*100
        mae = (window['low'].min()/entry - 1)*100
        trades.append(Trade(symbol, stage, x.index[entry_i], x.index[exit_i], entry, exit_px, gross, mfe, mae))
        i = exit_i + 1
    return trades


def metrics(trades: list[Trade], cost: float) -> dict:
    if not trades:
        return {'trades':0,'wr':0,'avg':0,'pf':0,'net':0,'mdd':0,'mfe':0,'mae':0}
    r = np.array([t.gross_pct-cost for t in trades], dtype=float)
    wins = r[r>0]
    losses = r[r<0]
    pf = wins.sum()/abs(losses.sum()) if len(losses) and abs(losses.sum())>1e-12 else (999 if len(wins) else 0)
    equity = np.cumprod(1+r/100)
    peak = np.maximum.accumulate(equity)
    mdd = ((equity/peak)-1).min()*100
    return {
        'trades':len(r),
        'wr':(r>0).mean()*100,
        'avg':r.mean(),
        'pf':pf,
        'net':(equity[-1]-1)*100,
        'mdd':mdd,
        'mfe':float(np.mean([t.mfe_pct for t in trades])),
        'mae':float(np.mean([t.mae_pct for t in trades])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB_DEFAULT))
    ap.add_argument('--symbols', default='AMD,AVGO,NVDA,SMCI,SOXL,SOXS,TQQQ,SQQQ,QQQ,SMH')
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]

    con = sqlite3.connect(args.db)
    all_stage: dict[str,list[Trade]] = {s:[] for s in ['F0','F1','F2','F3','F4']}
    coverage=[]
    for sym in syms:
        raw=load_bars(con,sym)
        if raw.empty:
            coverage.append((sym,0))
            continue
        x=add_features(resample5(raw))
        coverage.append((sym,len(x)))
        for stg in all_stage:
            tmp=x.copy()
            if stg=='F4':
                tmp['F4']=tmp['F3']
            all_stage[stg].extend(run_stage(sym,tmp,stg))
    con.close()

    print('FUJIMOTO_STAGED_BASELINE_V01')
    print('CLASS=TESTED_REIMPLEMENTATION  ORIGINAL_NOT_REPRODUCED')
    print('COVERAGE', ' '.join(f'{s}:{n}' for s,n in coverage))
    print('stage cost trades wr avg pf net mdd mfe mae')
    score=[]
    for stg,tr in all_stage.items():
        for cost in COSTS:
            m=metrics(tr,cost)
            print(f"{stg:>2} {cost:>4.2f} {m['trades']:>6d} {m['wr']:>6.2f} {m['avg']:>7.3f} {m['pf']:>7.3f} {m['net']:>8.3f} {m['mdd']:>8.3f} {m['mfe']:>7.3f} {m['mae']:>7.3f}")
            if abs(cost-0.20)<1e-9 and m['trades']>=20:
                score.append((m['net'],m['pf'],m['wr'],stg,m))
    if score:
        score.sort(reverse=True)
        net,pf,wr,stg,m=score[0]
        label='FIRST_CHAMPION_CANDIDATE' if net>0 and pf>1.05 else 'NO_CHAMPION_YET'
        print(f'RESULT {label} stage={stg} cost=0.20 net={net:.3f}% pf={pf:.3f} wr={wr:.2f}% trades={m["trades"]}')
    else:
        print('RESULT NO_CHAMPION_YET reason=INSUFFICIENT_TRADES')

if __name__=='__main__':
    main()
