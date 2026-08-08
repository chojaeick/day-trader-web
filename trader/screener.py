from __future__ import annotations
import pandas as pd
from .config import TradingConfig

WEIGHTS = {
    'above_ma5': 8,
    'ma5_slope': 7,
    'liquidity': 15,
    'rvol': 15,
    'atr': 10,
    'premarket': 5,
    'momentum': 8,
    'sector': 7,
    'index': 5,
    'breakout_room': 8,
    'catalyst': 7,
    'spread': 5,
}


def score_candidate(row: pd.Series, cfg: TradingConfig) -> tuple[int, dict]:
    s = 0
    parts = {}
    def add(name: str, ok: bool, fraction: float = 1.0):
        nonlocal s
        pts = round(WEIGHTS[name] * max(0.0, min(1.0, fraction))) if ok else 0
        s += pts
        parts[name] = pts

    add('above_ma5', row.price > row.ma5)
    add('ma5_slope', row.ma5_slope_pct > 0, min(row.ma5_slope_pct / 1.0, 1))
    add('liquidity', row.dollar_volume >= cfg.min_dollar_volume_usd,
        min(row.dollar_volume / (cfg.min_dollar_volume_usd * 3), 1))
    add('rvol', row.rvol >= cfg.min_rvol, min(row.rvol / 3.0, 1))
    add('atr', row.atr_pct >= 1.0, min(row.atr_pct / 4.0, 1))
    # Premarket is deliberately low-weight: bias/context, not a standalone trigger.
    add('premarket', abs(row.premarket_pct) >= 0.5, min(abs(row.premarket_pct) / 4.0, 1))
    add('momentum', abs(row.day_pct) >= 1.0, min(abs(row.day_pct) / 6.0, 1))
    add('sector', row.sector_strength > 0, min(abs(row.sector_strength) / 2.0, 1))
    add('index', row.index_strength > 0, min(abs(row.index_strength) / 1.5, 1))
    add('breakout_room', row.breakout_quality > 0.5, row.breakout_quality)
    add('catalyst', row.catalyst_score > 0, min(row.catalyst_score / 10.0, 1))
    add('spread', row.spread_pct <= 0.30, max(0, 1 - row.spread_pct / 0.30))

    # Bias: current momentum + sector/index. Inverse/short instruments can be ranked too.
    directional = row.day_pct + row.sector_strength + row.index_strength + 0.35 * row.premarket_pct
    bias = 'LONG' if directional >= 0 else 'SHORT'
    return min(s, 100), {'parts': parts, 'bias': bias}


def rank_candidates(df: pd.DataFrame, cfg: TradingConfig | None = None) -> pd.DataFrame:
    cfg = cfg or TradingConfig()
    x = df.copy()
    x = x[(x.price >= cfg.min_price_usd) & (x.dollar_volume >= cfg.min_dollar_volume_usd)]
    scores, biases = [], []
    for _, r in x.iterrows():
        score, meta = score_candidate(r, cfg)
        scores.append(score); biases.append(meta['bias'])
    x['score'] = scores; x['bias'] = biases
    return x.sort_values(['score','dollar_volume'], ascending=[False,False]).head(cfg.top_n).reset_index(drop=True)
