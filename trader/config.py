from dataclasses import dataclass

@dataclass(frozen=True)
class TradingConfig:
    # User trading profile v1.0
    day_trade_capital_krw: int = 30_000_000
    target_day_return_pct: float = 3.0   # goal, NOT take-profit cap
    hard_stop_pct: float = -2.0
    max_trades_per_day: int = 2

    # Screener
    min_price_usd: float = 5.0
    min_rvol: float = 1.5
    min_dollar_volume_usd: float = 50_000_000
    top_n: int = 10

    # Signal thresholds
    watch_score: int = 70
    setup_score: int = 82
    trigger_score: int = 90

    # Position sizing: 50/30/20
    entry_ladder = (0.50, 0.30, 0.20)

    # Profit management
    trim1_pct: float = 1.8
    trim1_fraction: float = 0.30
    trim2_pct: float = 3.0
    trim2_fraction: float = 0.30
    runner_fraction: float = 0.40
