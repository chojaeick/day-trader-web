from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tools.backtest_dbb_exit_lab import build_events, simulate
from tools.backtest_dbb_kr_v2_v21_v22 import load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT = Path('/home/ubuntu/day-trader-api')


def slope_pct(s: pd.Series, n: int) -> pd.Series:
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    den = float(np.dot(xc, xc))
    def f(a):
        a = np.asarray(a, dtype=float)
        if len(a) != n or not np.isfinite(a).all():
            return np.nan
        m = float(a.mean())
        if m == 0:
            return np.nan
        return float(np.dot(xc, a - m) / den) / abs(m)
    return pd.to_numeric(s, errors='coerce').rolling(n, min_periods=n).apply(f, raw=True)


def baseline(frames):
    return simulate(
        build_events(frames), frames,
        min_score=65.0,
        min_risk_pct=0.010,
        max_risk_pct=0.020,
        tp1_r=3.0,
        partial_fraction=0.75,
        structural_mode='CLOSE_BELOW_INNER_LOWER',
        runner_trail_pct=0.0,
        breakeven_after_tp1=True,
    )


def build_context(frames):
    out = {}
    for sym, f in frames.items():
        z = f[['time','open','high','low','close','mid','inner_upper','inner_lower']].copy().sort_values('time').reset_index(drop=True)
        z['mid_slope10'] = slope_pct(z['mid'], 10)
        z['price_slope5'] = slope_pct(z['close'], 5)
        z['price_slope10'] = slope_pct(z['close'], 10)

        # Causal 10-minute wave structure: compare recent 5 completed bars to previous 5.
        hi = pd.to_numeric(z['high'], errors='coerce')
        lo = pd.to_numeric(z['low'], errors='coerce')
        cl = pd.to_numeric(z['close'], errors='coerce')
        z['recent5_high'] = hi.rolling(5).max()
        z['recent5_low'] = lo.rolling(5).min()
        z['prev5_high'] = hi.shift(5).rolling(5).max()
        z['prev5_low'] = lo.shift(5).rolling(5).min()
        z['hh'] = z['recent5_high'] > z['prev5_high']
        z['hl'] = z['recent5_low'] > z['prev5_low']

        # Three nested, fully causal definitions. No future bar is used here.
        z['UP_MID'] = z['mid_slope10'] > 0
        z['UP_WAVE'] = z['UP_MID'] & z['hh'] & z['hl']
        z['UP_WAVE_MOM'] = z['UP_WAVE'] & (z['price_slope5'] > 0)

        iu = pd.to_numeric(z['inner_upper'], errors='coerce')
        il = pd.to_numeric(z['inner_lower'], errors='coerce')
        mid = pd.to_numeric(z['mid'], errors='coerce')
        z['band_location'] = np.select(
            [cl < il, cl < mid, cl < iu],
            ['BELOW_INNER_LOWER','INNER_LOWER_TO_MID','MID_TO_INNER_UPPER'],
            default='ABOVE_INNER_UPPER'
        )

        # Forward excursion is diagnostic only. It is never used to define the entry-time trend.
        for h in (5,10,20,30):
            future_hi = hi.shift(-1).rolling(h, min_periods=1).max().shift(-(h-1))
            future_lo = lo.shift(-1).rolling(h, min_periods=1).min().shift(-(h-1))
            z[f'mfe{h}'] = (future_hi / cl - 1.0) * 100.0
            z[f'mae{h}'] = (future_lo / cl - 1.0) * 100.0
        out[sym] = z
    return out


def attach(trades, ctx):
    chunks=[]
    t=trades.copy()
    t['entry_time']=pd.to_datetime(t['entry_time'])
    cols=['time','mid_slope10','price_slope5','price_slope10','hh','hl','UP_MID','UP_WAVE','UP_WAVE_MOM','band_location'] + [f'{x}{h}' for h in (5,10,20,30) for x in ('mfe','mae')]
    for sym,g in t.groupby('symbol', sort=False):
        c=ctx[sym][cols].rename(columns={'time':'ctx_time'}).sort_values('ctx_time')
        one=pd.merge_asof(g.sort_values('entry_time'), c, left_on='entry_time', right_on='ctx_time', direction='backward')
        chunks.append(one)
    return pd.concat(chunks, ignore_index=True)


def stats(name,g):
    s=summary(name,g)
    s['median_pct']=round(float(g.pnl_pct.median()),4) if len(g) else 0.0
    for h in (5,10,20,30):
        if len(g):
            s[f'mfe{h}_avg']=round(float(g[f'mfe{h}'].mean()),4)
            s[f'mfe{h}_gt_0_5']=round(float((g[f'mfe{h}']>=0.5).mean()*100),2)
            s[f'mfe{h}_gt_1_0']=round(float((g[f'mfe{h}']>=1.0).mean()*100),2)
            s[f'mae{h}_avg']=round(float(g[f'mae{h}'].mean()),4)
        else:
            s[f'mfe{h}_avg']=s[f'mfe{h}_gt_0_5']=s[f'mfe{h}_gt_1_0']=s[f'mae{h}_avg']=0.0
    return s


def print_group(title, t, key):
    rows=[]
    for k,g in t.groupby(key, dropna=False):
        r=stats(str(k),g); r[key]=str(k); rows.append(r)
    df=pd.DataFrame(rows)
    cols=[key,'trades','win_rate','avg_pct','gross_pct','pf','median_pct','mfe5_avg','mfe5_gt_0_5','mfe10_avg','mfe10_gt_0_5','mfe20_avg','mfe20_gt_0_5','mfe30_avg','mfe30_gt_0_5']
    print(f'\n=== {title} ===')
    print(df[[c for c in cols if c in df.columns]].to_string(index=False))
    return df


def main():
    raw=load_data()
    frames=build_frames_cached(raw, workers=2, rebuild=False)
    trades=baseline(frames)
    ctx=build_context(frames)
    t=attach(trades,ctx)

    print(f'[CONTROL] trades={len(t)} win={(t.pnl_pct>0).mean()*100:.2f}% gross={t.pnl_pct.sum():+.4f}% avg={t.pnl_pct.mean():+.4f}%')
    print('[CAUSAL TREND] UP_MID: DBB mid 10-bar slope>0; UP_WAVE: UP_MID + recent5 HH + recent5 HL; UP_WAVE_MOM: UP_WAVE + price 5-bar slope>0')
    print('[IMPORTANT] Forward MFE/MAE is diagnostic only and is NOT used to label trend.')

    bool_rows=[]
    for col in ['UP_MID','UP_WAVE','UP_WAVE_MOM']:
        for val in [True,False]:
            g=t[t[col].fillna(False)==val]
            r=stats(f'{col}={val}',g); r['condition']=f'{col}={val}'; bool_rows.append(r)
    b=pd.DataFrame(bool_rows)
    cols=['condition','trades','win_rate','avg_pct','gross_pct','pf','median_pct','mfe5_avg','mfe5_gt_0_5','mfe10_avg','mfe10_gt_0_5','mfe20_avg','mfe20_gt_0_5','mfe30_avg','mfe30_gt_0_5']
    print('\n=== TREND TRUTH CHECK ===')
    print(b[cols].to_string(index=False))

    wave=t[t['UP_WAVE'].fillna(False)].copy()
    loc=print_group('UP_WAVE PERFORMANCE BY ENTRY BAND LOCATION', wave, 'band_location')

    # The user's key intuition: if the engine buys a pullback while the wave is up, does price actually rebound?
    pullback=wave[wave['band_location'].isin(['BELOW_INNER_LOWER','INNER_LOWER_TO_MID','MID_TO_INNER_UPPER'])].copy()
    print('\n=== KEY CHECK: UP_WAVE + ENTRY NOT ABOVE INNER_UPPER ===')
    if len(pullback):
        r=stats('UP_WAVE_PULLBACK',pullback)
        print(pd.DataFrame([r])[['trades','win_rate','avg_pct','gross_pct','pf','mfe5_avg','mfe5_gt_0_5','mfe5_gt_1_0','mfe10_avg','mfe10_gt_0_5','mfe10_gt_1_0','mfe20_avg','mfe20_gt_0_5','mfe20_gt_1_0','mfe30_avg','mfe30_gt_0_5','mfe30_gt_1_0']].to_string(index=False))
    else:
        print('no trades')

    t.to_csv(OUT/'dbb_upwave_truth_trades.csv',index=False)
    b.to_csv(OUT/'dbb_upwave_truth_summary.csv',index=False)
    loc.to_csv(OUT/'dbb_upwave_truth_band_location.csv',index=False)
    print('\n[CSV] dbb_upwave_truth_trades.csv, dbb_upwave_truth_summary.csv, dbb_upwave_truth_band_location.csv')

if __name__=='__main__':
    main()
