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
    """Causal 5m state knowable immediately before probe_ts.

    Raw 1m rows are open-time stamped in this backtest path, so rows with time
    >= probe_ts are not yet completed and are excluded. The current incomplete
    5m bucket is emitted as a provisional candle at probe_ts.
    """
    x = bars.copy().sort_values('time')
    x['time'] = pd.to_datetime(x['time'])
    probe_ts = pd.Timestamp(probe_ts)
    x = x[x['time'] < probe_ts].copy()
    if x.empty:
        return pd.DataFrame(columns=['time','open','high','low','close','volume'])

    x['bucket'] = x['time'].dt.floor('5min')
    z = x.groupby('bucket', sort=True).agg(
        open=('open','first'), high=('high','max'), low=('low','min'),
        close=('close','last'), volume=('volume','sum'), rows=('close','size')
    ).reset_index()
    if z.empty:
        return pd.DataFrame(columns=['time','open','high','low','close','volume'])

    last_bucket = z.iloc[-1]['bucket']
    z['time'] = [
        probe_ts if (r.bucket == last_bucket and int(r.rows) < 5)
        else pd.Timestamp(r.bucket) + pd.Timedelta(minutes=5)
        for r in z.itertuples(index=False)
    ]
    return z[['time','open','high','low','close','volume']].reset_index(drop=True)


def score_at(bars: pd.DataFrame, probe_ts: pd.Timestamp, cfg: DoubleBollingerEngine5Config):
    p5 = provisional_5m(bars, probe_ts)
    if len(p5) < max(30, int(cfg.bb_period) + 5):
        return None
    eng = DoubleBollingerEngine5(cfg)
    f = v10._refine_entry_frame(eng.enrich(p5))
    s = reweight({'X': f}, cfg, 0.0)['X']
    if s.empty:
        return None
    r = s.iloc[-1]
    out = {
        'probe_time': pd.Timestamp(probe_ts),
        'price': _finite(r.get('close', np.nan)),
        'live_score': _finite(r.get('entry_score', np.nan)),
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
    print('=== V22 KR PRE-ENTRY MINUTE SCORE DIAGNOSTIC v2 ===', flush=True)
    print('Each T-k score is the causal LIVE provisional score knowable at that minute.', flush=True)
    print('IMPORTANT: event_score is a transported source-event score and is NOT required to equal live_score at entry time.', flush=True)
    print('V20 wait/reaccel can transport an earlier score forward; SLOW_TURN/V_REBOUND also use source-specific event semantics.', flush=True)

    raw = {n(k): v for k, v in load_data().items()}
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

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

    source_map, event_score_map = {}, {}
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
        event_score = event_score_map.get(key, np.nan)
        for off in OFFSETS:
            pt = et + pd.Timedelta(minutes=off)
            rec = score_at(raw[sym], pt, cfg)
            if rec is None:
                rec = {'probe_time': pt, 'price': np.nan, 'live_score': np.nan,
                       'trend_up': False, 'entry_gate': False,
                       'macd_strength': np.nan, 'rsi_strength': np.nan,
                       **{c: np.nan for c in SCORE_COMPONENTS}}
            rec.update({
                'trade_id': int(i), 'symbol': sym, 'source': source,
                'entry_time': et, 'offset_min': int(off),
                'actual_entry_price': float(tr.entry_price),
                'event_score': event_score,
                'trade_pnl_pct': float(tr.pnl_pct),
                'trade_reason': tr.reason,
            })
            rows.append(rec)

    detail = pd.DataFrame(rows).sort_values(['trade_id','offset_min']).reset_index(drop=True)
    detail['score_delta_1m'] = detail.groupby('trade_id')['live_score'].diff()

    idx = ['trade_id','symbol','source','entry_time','actual_entry_price','event_score','trade_pnl_pct','trade_reason']
    wide = detail.pivot(index=idx, columns='offset_min', values='live_score').reset_index()
    wide = wide.rename(columns={-3:'score_t_3', -2:'score_t_2', -1:'score_t_1', 0:'score_t'})
    for c in ['score_t_3','score_t_2','score_t_1','score_t']:
        if c not in wide.columns:
            wide[c] = np.nan

    wide['slope_3_to_2'] = wide['score_t_2'] - wide['score_t_3']
    wide['slope_2_to_1'] = wide['score_t_1'] - wide['score_t_2']
    wide['slope_1_to_t'] = wide['score_t'] - wide['score_t_1']
    wide['rise_3m'] = wide['score_t'] - wide['score_t_3']
    wide['last_jump_abs'] = wide['slope_1_to_t']
    denom = wide['rise_3m'].abs().replace(0.0, np.nan)
    wide['last_jump_share'] = wide['slope_1_to_t'].clip(lower=0.0) / denom
    wide['steady_up_3'] = (
        (wide['slope_3_to_2'] >= 0.0) &
        (wide['slope_2_to_1'] >= 0.0) &
        (wide['slope_1_to_t'] >= 0.0)
    )
    wide['late_spike_20'] = wide['slope_1_to_t'] >= 20.0
    wide['late_spike_30'] = wide['slope_1_to_t'] >= 30.0

    print('\n=== SCORE PATHS: T-3 / T-2 / T-1 / T ===')
    cols = ['trade_id','symbol','source','entry_time','score_t_3','score_t_2','score_t_1','score_t',
            'event_score','slope_3_to_2','slope_2_to_1','slope_1_to_t','rise_3m',
            'last_jump_share','steady_up_3','late_spike_20','late_spike_30','trade_pnl_pct','trade_reason']
    print(wide[cols].to_string(index=False))

    winners = wide[wide.trade_pnl_pct > integ.FEE_RT_PCT]
    losers = wide[wide.trade_pnl_pct <= integ.FEE_RT_PCT]

    def block(label, q):
        print(f'\n=== {label} ===')
        stat_cols = ['score_t_3','score_t_2','score_t_1','score_t','slope_3_to_2','slope_2_to_1',
                     'slope_1_to_t','rise_3m','last_jump_share']
        print(q[stat_cols].mean(numeric_only=True).to_string())
        print('steady_up_3_pct=', round(float(q.steady_up_3.mean()*100.0), 2) if len(q) else np.nan)
        print('late_spike_20_pct=', round(float(q.late_spike_20.mean()*100.0), 2) if len(q) else np.nan)
        print('late_spike_30_pct=', round(float(q.late_spike_30.mean()*100.0), 2) if len(q) else np.nan)

    block('ALL', wide)
    block('WINNERS', winners)
    block('LOSERS', losers)

    print('\n=== SOURCE-AWARE EVENT SCORE NOTE ===')
    print('event_score is retained only for provenance. Do not use live_score-event_score as a correctness test.')
    for src, q in wide.groupby('source'):
        print(src, 'trades=', len(q), 'avg_live_T=', round(float(q.score_t.mean()), 4), 'avg_event=', round(float(q.event_score.mean()), 4))

    detail.to_csv(OUT / 'minute_score_detail_v2.csv', index=False)
    wide.to_csv(OUT / 'minute_score_paths_v2.csv', index=False)
    print('\nWROTE', OUT / 'minute_score_detail_v2.csv')
    print('WROTE', OUT / 'minute_score_paths_v2.csv')


if __name__ == '__main__':
    main()
