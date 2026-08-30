from __future__ import annotations

"""V3 diagnostic: add causal RSI-50 cross weighting on top of V2 Slow-turn episode score."""

from pathlib import Path
import numpy as np
import pandas as pd

import tools.validate_engine5_integrated_slow_turn_transition_score_v2 as v2

OUT = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')
DETAIL = OUT / 'slow_turn_transition_score_v2_detail.csv'


def num(x): return pd.to_numeric(x, errors='coerce')

def clip01(x):
    try:
        return float(np.clip(float(x), 0.0, 1.0))
    except Exception:
        return 0.0


def rsi50_bonus(r):
    """Reward a NEW fast RSI cross through 50 from below.

    Uses episode RSI gain/speed already produced by V2. The bonus is only eligible
    when the turn started below 50 and the episode reached/crossed 50; mere RSI>50
    after a long gradual rise gets no bonus.
    """
    start = float(r.get('rsi_turn_start_value', np.nan)) if pd.notna(r.get('rsi_turn_start_value', np.nan)) else np.nan
    end = float(r.get('rsi_turn_end_value', np.nan)) if pd.notna(r.get('rsi_turn_end_value', np.nan)) else np.nan
    gain = float(r.get('rsi_episode_gain', 0.0)) if pd.notna(r.get('rsi_episode_gain', np.nan)) else 0.0
    speed = float(r.get('rsi_episode_speed', 0.0)) if pd.notna(r.get('rsi_episode_speed', np.nan)) else 0.0

    crossed = np.isfinite(start) and np.isfinite(end) and start < 50.0 <= end
    if not crossed:
        return 0.0, False

    rise_to_50 = max(50.0 - start, 0.0)
    # Full bonus requires both a meaningful move and a fast move.
    move_strength = clip01(rise_to_50 / 15.0)
    speed_strength = clip01(speed / 4.0)
    bonus = 20.0 * min(move_strength, speed_strength)
    return bonus, True


def main():
    if not DETAIL.exists():
        raise FileNotFoundError(DETAIL)
    x = pd.read_csv(DETAIL)

    # V2 did not persist RSI start/end values in the first revision. If absent,
    # stop explicitly instead of fabricating a bonus.
    needed = {'rsi_turn_start_value','rsi_turn_end_value','rsi_episode_gain','rsi_episode_speed','transition_score'}
    missing = sorted(needed - set(x.columns))
    if missing:
        print('MISSING_COLUMNS', missing)
        print('V2 must be patched to persist RSI turn start/end values before RSI-50 scoring can be trusted.')
        raise SystemExit(2)

    bonuses=[]; crossed=[]
    for _, r in x.iterrows():
        b,c = rsi50_bonus(r); bonuses.append(b); crossed.append(c)
    x['rsi50_bonus'] = bonuses
    x['rsi50_crossed'] = crossed
    x['transition_score_v3'] = num(x['transition_score']) + num(x['rsi50_bonus'])

    print('=== SLOW-TURN V3 RSI-50 CROSS AUDIT ===')
    targets=[('058610','2026-08-13 09:25:00+09:00','V_TURN_SUCCESS'),('122630','2026-08-20 13:06:00+09:00','GRADUAL_FAILURE'),('950160','2026-08-14 10:59:00+09:00','VALID_SLOW_SUCCESS')]
    x['entry_time']=pd.to_datetime(x['entry_time'])
    for sym,t,label in targets:
        q=x[(x.symbol.astype(str).str.zfill(6)==sym)&(x.entry_time==pd.Timestamp(t))]
        print(f'\n[{label}]')
        if q.empty:
            print('NOT FOUND'); continue
        r=q.iloc[0]
        for c in ['transition_score','rsi_turn_start_value','rsi_turn_end_value','rsi_episode_gain','rsi_episode_speed','rsi50_crossed','rsi50_bonus','transition_score_v3','net_pct','result']:
            if c in q.columns: print(f'{c:24s} {r[c]}')

    out=OUT/'slow_turn_transition_score_v3_rsi50_detail.csv'
    x.to_csv(out,index=False)
    print('\nWROTE',out)

if __name__=='__main__': main()
