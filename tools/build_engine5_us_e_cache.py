from __future__ import annotations

"""Build the US Engine5 E-series cache from native US market data.

E-series invariants:
- US REGULAR bars stay in exchange-local ET (09:30..15:59).
- OHLC stays in native USD. No FX conversion and no KRW-equivalent remapping.
- Volume is unchanged.
- 1m/5m RSI, MACD, Bollinger and all derived Engine5 features are rebuilt from USD OHLCV.
- US session-clock adaptation belongs to the E-series engine/validator, not the DB/cache.

This cache is intentionally separate from the historical us_kr_mapped cache.
"""

import argparse
import pickle
import shutil
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

DB = Path('/home/ubuntu/day-trader-api/daytrader.db')
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_us_e_cache')
CORE = OUT_DIR / 'us_e_core.pkl'
AUDIT = OUT_DIR / 'us_e_input_audit.csv'
PROV_DIR = OUT_DIR / 'provisional'
SYMS = ['SOXL','TQQQ','QQQ','NVDA','AMD','SMH','SPY','AVGO','PLTR']
NY = 'America/New_York'
CACHE_SCHEMA = 'US_E_USD_ET_V1'
PROV_REQUIRED = {'time','bucket_end','close','mid_slope8','gap_delta','macd_slope','rsi_slope','strength_rel'}


def key(s): return str(s).zfill(6)
def provisional_path(sym): return PROV_DIR / f'{key(sym)}_provisional.pkl'


def load_native_usd_et():
    con = sqlite3.connect(DB)
    out, audits = {}, []
    for i, s in enumerate(SYMS, 1):
        q = pd.read_sql_query(
            "select trade_date,et_time,open,high,low,close,volume from historical_minute_bars "
            "where symbol=? and interval_min=1 and session='REGULAR' order by trade_date,et_time",
            con, params=(s,))
        if q.empty:
            print(f'[{i}/{len(SYMS)}] {s} EMPTY', flush=True)
            continue
        q['time'] = pd.to_datetime(q.et_time, utc=True).dt.tz_convert(NY)
        for c in ['open','high','low','close','volume']:
            q[c] = pd.to_numeric(q[c], errors='coerce')
        q = q.dropna(subset=['time','open','high','low','close']).sort_values('time').reset_index(drop=True)
        out[key(s)] = q[['time','open','high','low','close','volume']].copy()
        q['_day'] = q.time.dt.date
        for d, g in q.groupby('_day'):
            f5 = to_5m(g[['time','open','high','low','close','volume']].copy())
            audits.append(dict(
                symbol=s, day=str(d), rows1=len(g), first1=str(g.time.iloc[0]), last1=str(g.time.iloc[-1]),
                bars5=len(f5), first5=str(f5.time.iloc[0]) if len(f5) else '',
                last5=str(f5.time.iloc[-1]) if len(f5) else '', dup=int(g.time.duplicated().sum()),
                first_close_usd=float(g.close.iloc[0]), last_close_usd=float(g.close.iloc[-1])
            ))
        print(f'[{i}/{len(SYMS)}] {s} rows={len(q)} USD/ET {q.time.min()} -> {q.time.max()}', flush=True)
    con.close()
    return out, pd.DataFrame(audits)


def valid_provisional(path):
    if not path.exists(): return None
    try:
        with path.open('rb') as fh: pf = pickle.load(fh)
        if not isinstance(pf, pd.DataFrame) or pf.empty: return None
        if not PROV_REQUIRED.issubset(pf.columns): return None
        return pf
    except Exception:
        return None


def valid_core(core):
    req = {'raw','cfg','completed','scored','strength','micros','packed','states'}
    return (
        isinstance(core, dict) and req.issubset(core)
        and core.get('cache_schema') == CACHE_SCHEMA
        and core.get('time_shift_minutes') == 0
        and core.get('price_unit') == 'USD'
        and core.get('fx_applied') is False
    )


def build_provisional_cache(core):
    PROV_DIR.mkdir(parents=True, exist_ok=True)
    raw, cfg, completed = core['raw'], core['cfg'], core['completed']
    print('\nBUILD/RESUME E-SERIES PROVISIONAL CACHE...', flush=True)
    hit = built = 0
    for i, s in enumerate(raw, 1):
        p = provisional_path(s)
        existing = valid_provisional(p)
        if existing is not None:
            hit += 1
            print(f'[{i}/{len(raw)}] {s} HIT rows={len(existing)}', flush=True)
            continue
        pf = uscache.build_minimal_provisional_fast(raw[s], cfg, completed[s])
        tmp = p.with_suffix('.tmp')
        with tmp.open('wb') as fh: pickle.dump(pf, fh, pickle.HIGHEST_PROTOCOL)
        tmp.replace(p)
        built += 1
        print(f'[{i}/{len(raw)}] {s} BUILT rows={len(pf)}', flush=True)
    print(f'PROVISIONAL READY: hit={hit} built={built} total={len(raw)}', flush=True)


def build_core():
    raw, audit = load_native_usd_et()
    if not raw: raise SystemExit('NO US DATA')
    print('\n=== E-SERIES USD/ET INPUT AUDIT ===')
    print(f"day-symbol={len(audit)} rows1 median={audit.rows1.median():.1f} min={audit.rows1.min()} max={audit.rows1.max()} full390={(audit.rows1==390).sum()}/{len(audit)}")
    print(f"bars5 median={audit.bars5.median():.1f} full78={(audit.bars5==78).sum()}/{len(audit)} duplicates={audit.dup.sum()}")
    print('sample', audit.iloc[0].to_dict())
    audit.to_csv(AUDIT, index=False)

    cfg0 = DoubleBollingerEngine5Config()
    cfg = replace(cfg0, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)
    print('\nBUILD USD-NATIVE E CORE...', flush=True)
    packed = v8.base.pack_exit_events(raw, cfg0)
    states = base.pack_state_events(base.build_cfg_frames(raw, cfg0))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {key(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {key(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(b, cfg) for s, b in raw.items()}
    core = dict(
        raw=raw, cfg0=cfg0, cfg=cfg, packed=packed, states=states,
        scored=scored, strength=strength, completed=completed, micros=micros,
        price_unit='USD', fx_applied=False, time_shift_minutes=0,
        session='US_REGULAR_ET', cache_schema=CACHE_SCHEMA,
    )
    tmp = CORE.with_suffix('.tmp')
    with tmp.open('wb') as fh: pickle.dump(core, fh, pickle.HIGHEST_PROTOCOL)
    tmp.replace(CORE)
    print('WROTE', CORE, flush=True)
    print('WROTE', AUDIT, flush=True)
    return core


def purge():
    print('PURGE OLD E CACHE...', flush=True)
    if CORE.exists(): CORE.unlink()
    if AUDIT.exists(): AUDIT.unlink()
    if PROV_DIR.exists(): shutil.rmtree(PROV_DIR)
    for p in OUT_DIR.glob('us_e_*'):
        if p not in (CORE, AUDIT) and p.is_file(): p.unlink()
    print('OLD E CACHE/RESULTS REMOVED.', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true')
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print('=== BUILD/RESUME ENGINE5 E-SERIES CACHE ===', flush=True)
    print('US REGULAR 390m | original ET | native USD | NO FX CONVERSION', flush=True)
    if args.rebuild: purge()
    core = None
    if CORE.exists():
        try:
            with CORE.open('rb') as fh: candidate = pickle.load(fh)
            if not valid_core(candidate): raise ValueError('invalid E cache schema')
            core = candidate
            print(f"CORE HIT symbols={len(core['raw'])} rows={sum(len(x) for x in core['raw'].values())}", flush=True)
        except Exception as e:
            print('CORE INVALID, REBUILD:', repr(e), flush=True)
            if PROV_DIR.exists(): shutil.rmtree(PROV_DIR)
            core = build_core()
    if core is None: core = build_core()
    build_provisional_cache(core)
    print('\nE CACHE READY.', flush=True)
    print('Native USD preserved. Original ET preserved. No FX mapping exists in E-series.', flush=True)


if __name__ == '__main__': main()
