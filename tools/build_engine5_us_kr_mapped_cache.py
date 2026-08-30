from __future__ import annotations

"""Build persistent US Engine5 input/cache in KR-equivalent units/clock semantics.

Purpose: external validation of KR-designed Engine5 logic on US data without
changing the strategy. US regular-session data are mapped at the INPUT layer:
- keep all 390 regular-session minutes;
- shift exchange-local clock by -30 minutes so US 09:30..15:59 maps to the
  engine's KR 09:00..15:29 clock semantics;
- multiply OHLC by FX=1400 so all price-linear indicators/thresholds (MACD,
  Bollinger widths, absolute stop distances, etc.) are in KRW-equivalent units;
- leave volume unchanged;
- preserve relative returns/RSI/ratios by construction.

The expensive market-independent derived data are persisted once:
- us_kr_mapped_core.pkl: raw mapped 1m + packed exits + 5m/scored/strength + 1m micro
- provisional/<symbol>_provisional.pkl: causal provisional 5m features used by
  Slow-turn and V-rebound

Repeated strategy validation must read these caches instead of rebuilding DB,
RSI/MACD/Bollinger/1m/5m features.
"""

import pickle
import sqlite3
from dataclasses import replace
from pathlib import Path

import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.build_engine5_us_oos_cache as uscache
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight, to_5m

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
OUT_DIR=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
CORE=OUT_DIR/'us_kr_mapped_core.pkl'
AUDIT=OUT_DIR/'us_kr_mapped_input_audit.csv'
PROV_DIR=OUT_DIR/'provisional'
SYMS=['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
FX=1400.0
NY='America/New_York'
SHIFT=pd.Timedelta(minutes=30)

def key(s): return str(s).zfill(6)

def provisional_path(sym): return PROV_DIR/f'{key(sym)}_provisional.pkl'

def load_mapped():
    con=sqlite3.connect(DB)
    out={}; audits=[]
    for i,s in enumerate(SYMS,1):
        q=pd.read_sql_query(
            "select trade_date,et_time,open,high,low,close,volume from historical_minute_bars "
            "where symbol=? and interval_min=1 and session='REGULAR' order by trade_date,et_time",
            con,params=(s,))
        if q.empty:
            print(f'[{i}/{len(SYMS)}] {s} EMPTY',flush=True); continue
        et=pd.to_datetime(q.et_time,utc=True).dt.tz_convert(NY)
        q['original_et_time']=et
        q['time']=et-SHIFT
        for c in ['open','high','low','close']:
            q[c]=pd.to_numeric(q[c],errors='coerce')*FX
        q['volume']=pd.to_numeric(q.volume,errors='coerce')
        q=q.dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)
        out[key(s)]=q[['time','open','high','low','close','volume']].copy()

        q['_day']=q.time.dt.date
        for d,g in q.groupby('_day'):
            f5=to_5m(g[['time','open','high','low','close','volume']].copy())
            audits.append(dict(symbol=s,day=str(d),rows1=len(g),first1=str(g.time.iloc[0]),last1=str(g.time.iloc[-1]),
                               bars5=len(f5),first5=str(f5.time.iloc[0]) if len(f5) else '',last5=str(f5.time.iloc[-1]) if len(f5) else '',
                               dup=int(g.time.duplicated().sum())))
        print(f'[{i}/{len(SYMS)}] {s} rows={len(q)} mapped {q.time.min()} -> {q.time.max()}',flush=True)
    con.close()
    return out,pd.DataFrame(audits)

def build_provisional_cache(core):
    PROV_DIR.mkdir(parents=True,exist_ok=True)
    raw=core['raw']; cfg=core['cfg']; completed=core['completed']
    print('\nBUILD PERSISTENT PROVISIONAL CACHE...',flush=True)
    for i,s in enumerate(raw,1):
        p=provisional_path(s)
        pf=uscache.build_minimal_provisional_fast(raw[s],cfg,completed[s])
        with p.open('wb') as fh: pickle.dump(pf,fh,pickle.HIGHEST_PROTOCOL)
        print(f'[{i}/{len(raw)}] {s} rows={len(pf)} -> {p}',flush=True)

def main():
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    print('=== BUILD PERSISTENT US -> KR ENGINE CACHE ===',flush=True)
    print('REGULAR 390m kept | clock -30m | OHLC x1400 | volume unchanged',flush=True)
    raw,audit=load_mapped()
    if not raw: raise SystemExit('NO US DATA')

    print('\n=== MAPPING AUDIT ===')
    print(f"day-symbol={len(audit)} rows1 median={audit.rows1.median():.1f} min={audit.rows1.min()} max={audit.rows1.max()} full390={(audit.rows1==390).sum()}/{len(audit)}")
    print(f"bars5 median={audit.bars5.median():.1f} full78={(audit.bars5==78).sum()}/{len(audit)} duplicates={audit.dup.sum()}")
    print('sample',audit.iloc[0].to_dict())
    audit.to_csv(AUDIT,index=False)

    cfg0=DoubleBollingerEngine5Config()
    cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    print('\nBUILD MAPPED CORE...',flush=True)
    packed=v8.base.pack_exit_events(raw,cfg0)
    states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg)
    f10={key(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={key(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    completed={s:rt.add_completed_strength(f) for s,f in scored.items()}
    micros={s:h.build_micro(b,cfg) for s,b in raw.items()}
    core=dict(raw=raw,cfg0=cfg0,cfg=cfg,packed=packed,states=states,scored=scored,strength=strength,completed=completed,micros=micros,fx=FX,time_shift_minutes=-30)
    with CORE.open('wb') as fh: pickle.dump(core,fh,pickle.HIGHEST_PROTOCOL)
    print('WROTE',CORE,flush=True)
    print('WROTE',AUDIT,flush=True)

    build_provisional_cache(core)
    print('\nCACHE READY.',flush=True)
    print('Repeated validations should load CORE + provisional/*.pkl only.',flush=True)

if __name__=='__main__': main()
