from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_v20_regime_transition as rt
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5, DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

OUT = Path('/home/ubuntu/day-trader-api/engine5_v22_preentry_minute_scores')
OFFSETS = [-3, -2, -1, 0]
SCORE_COMPONENTS = [
    'score_trend', 'score_macd_state', 'score_macd_gap', 'score_golden',
    'score_rsi_state', 'score_rsi_accel', 'score_volume',
    'score_outer_expand', 'score_inner_traverse',
]


def n(x):
    return str(x).zfill(6)


def _finite(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else np.nan
    except Exception:
        return np.nan


def provisional_5m(bars: pd.DataFrame, probe_ts: pd.Timestamp) -> pd.DataFrame:
    """Build a causal 5m history as it would be knowable at probe_ts.

    Raw 1m bars with timestamp >= probe_ts are excluded. Completed 5m buckets keep
    the normal bucket+5m timestamp. The last incomplete bucket, if any, is emitted
    as a provisional candle timestamped at probe_ts. No future 1m data is used.
    """
    x = bars.copy().sort_values('time')
    x['time'] = pd.to_datetime(x['time'])
    probe_ts = pd.Timestamp(probe_ts)
    x = x[x['time'] < probe_ts].copy()
    if x.empty:
        return pd.DataFrame(columns=['time','open','high','low','close','volume'])

    x['bucket'] = x['time'].dt.floor('5min')
    g = x.groupby('bucket', sort=True)
    z = g.agg(
        open=('open','first'), high=('high','max'), low=('low','min'),
        close=('close','last'), volume=('volume','sum'), rows=('close','size')
    ).reset_index()
    if z.empty:
        return pd.DataFrame(columns=['time','open','high','low','close','volume'])

    last_bucket = z.iloc[-1]['bucket']
    times = []
    for r in z.itertuples(index=False):
        if r.bucket == last_bucket and int(r.rows) < 5:
            times.append(probe_ts)
        else:
            times.append(pd.Timestamp(r.bucket) + pd.Timedelta(minutes=5))
    z['time'] = times
    return z[['time','open','high','low','close','volume']].reset_index(drop=True)


def score_at(bars: pd.DataFrame, probe_ts: pd.Timestamp, cfg: DoubleBollingerEngine5Config):
    p5 = provisional_5m(bars, probe_ts)
    if len(p5) < max(30, int(cfg.bb_period) + 5):
        return None
    eng = DoubleBollingerEngine5(cfg)
    f = eng.enrich(p5)
    f = v10._refine_entry_frame(f)
    s = reweight({'X': f}, cfg, 0.0)['X']
    if s.empty:
        return None
    r = s.iloc[-1]
    out = {
        'probe_time': pd.Timestamp(probe_ts),
        'price': _finite(r.get('close', np.nan)),
        'entry_score': _finite(r.get('entry_score', np.nan)),
        'trend_up': bool(r.get('trend_up', False)),
        'entry_gate': bool(r.get('entry_gate', False)) if 'entry_gate' in s.columns else False,
        'macd_strength': _finite(r.get('macd_slope_spread_strength', np.nan)),
        'rsi_strength': _finite(r.get('rsi_slope_strength', np.nan)),
    }
    for c in SCORE_COMPONENTS:
        out[c] = _finite(r.get(c, np.nan))
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print('=== V22 KR PRE-ENTRY MINUTE SCORE DIAGNOSTIC ===', flush=True)
    print('Each probe is causal: raw 1m bars timestamped at/after probe time are excluded.', flush=True)
    print('T-3/T-2/T-1 use a provisional current 5m candle; T must reproduce the actual V22 score.', flush=True)

    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    # Reconstruct the exact V22 source set and actual executed baseline trades.
    packed = base.v8.base.pack_exit_events(raw, base_cfg) if hasattr(base, 'v8') else None
    # Use the same public helpers explicitly to avoid relying on module aliases.
    import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames0 = base.build_cfg_frames(raw, cfg)
    f10 = {n(s): v10._refine_entry_frame(f) for s, f in frames0.items()}
    scored = {n(s): f for s, f in reweight(f10, cfg, 0.0).items()}
    strength = {s: ms.add_strength(f) for s, f in scored.items()}
    completed = {s: rt.add_completed_strength(f) for s, f in scored.items()}
    micros = {s: h.build_micro(raw[s], cfg) for s in raw}
    tagged = integ.build_sources(raw, cfg, scored, strength, completed, micros)
    trades = integ.simulate(packed, states, tagged)

    source_map = {}
    event_score_map = {}
    for item in tagged:
        key = (n(item['symbol']), pd.Timestamp(item['time']))
        source_map.setdefault(key, item['source'])
        event_score_map.setdefault(key, float(item['event'][2]))

    rows = []
    for i, tr in trades.reset_index(drop=True).iterrows():
        sym = n(tr.symbol)
        et = pd.Timestamp(tr.entry_time)
        key = (sym, et)
        source = source_map.get(key, 'UNKNOWN')
        actual_event_score = event_score_map.get(key, np.nan)
        for off in OFFSETS:
            pt = et + pd.Timedelta(minutes=off)
            rec = score_at(raw[sym], pt, cfg)
            if rec is None:
                rec = {'probe_time': pt, 'price': np.nan, 'entry_score': np.nan, 'trend_up': False,
                       'entry_gate': False, 'macd_strength': np.nan, 'rsi_strength': np.nan,
                       **{c: np.nan for c in SCORE_COMPONENTS}}
            rec.update({
                'trade_id': int(i), 'symbol': sym, 'source': source,
                'entry_time': et, 'offset_min': int(off),
                'actual_entry_price': float(tr.entry_price),
                'actual_event_score': actual_event_score,
                'trade_pnl_pct': float(tr.pnl_pct),
                'trade_reason': tr.reason,
            })
            rows.append(rec)

    detail = pd.DataFrame(rows)
    detail['score_delta_1m'] = detail.sort_values(['trade_id','offset_min']).groupby('trade_id')['entry_score'].diff()

    wide = detail.pivot(index=['trade_id','symbol','source','entry_time','actual_entry_price','actual_event_score','trade_pnl_pct','trade_reason'],
                        columns='offset_min', values='entry_score').reset_index()
    wide = wide.rename(columns={-3:'score_t_3', -2:'score_t_2', -1:'score_t_1', 0:'score_t'})
    for c in ['score_t_3','score_t_2','score_t_1','score_t']:
        if c not in wide.columns:
            wide[c] = np.nan
    wide['slope_3_to_2'] = wide['score_t_2'] - wide['score_t_3']
    wide['slope_2_to_1'] = wide['score_t_1'] - wide['score_t_2']
    wide['slope_1_to_t'] = wide['score_t'] - wide['score_t_1']
    wide['rise_3m'] = wide['score_t'] - wide['score_t_3']
    wide['t_repro_diff'] = wide['score_t'] - wide['actual_event_score']

    max_diff = pd.to_numeric(wide['t_repro_diff'], errors='coerce').abs().max()
    repro = bool(np.isfinite(max_diff) and max_diff < 1e-6)
    print('\nT SCORE REPRO CHECK:', 'PASS' if repro else 'FAIL', 'max_abs_diff=', max_diff)
    if not repro:
        print('WARNING: provisional T score does not exactly match actual event score; inspect timestamp semantics before using T-1/T-2/T-3.', flush=True)

    print('\n=== SCORE PATHS: T-3 / T-2 / T-1 / T ===')
    cols = ['trade_id','symbol','source','entry_time','score_t_3','score_t_2','score_t_1','score_t','actual_event_score','slope_3_to_2','slope_2_to_1','slope_1_to_t','rise_3m','trade_pnl_pct','trade_reason']
    print(wide[cols].to_string(index=False))

    print('\n=== AVERAGE SCORE PATH ===')
    print(wide[['score_t_3','score_t_2','score_t_1','score_t','rise_3m']].mean(numeric_only=True).to_string())

    winners = wide[wide.trade_pnl_pct > integ.FEE_RT_PCT]
    losers = wide[wide.trade_pnl_pct <= integ.FEE_RT_PCT]
    print('\n=== WINNERS AVG ===')
    print(winners[['score_t_3','score_t_2','score_t_1','score_t','rise_3m']].mean(numeric_only=True).to_string())
    print('\n=== LOSERS AVG ===')
    print(losers[['score_t_3','score_t_2','score_t_1','score_t','rise_3m']].mean(numeric_only=True).to_string())

    detail.to_csv(OUT / 'minute_score_detail.csv', index=False)
    wide.to_csv(OUT / 'minute_score_paths.csv', index=False)
    print('\nWROTE', OUT / 'minute_score_detail.csv')
    print('WROTE', OUT / 'minute_score_paths.csv')


if __name__ == '__main__':
    main()
