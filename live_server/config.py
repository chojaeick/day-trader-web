from __future__ import annotations
import os
from dataclasses import dataclass, field

def _symbols(raw: str) -> list[str]:
    return [x.strip().upper() for x in raw.split(',') if x.strip()]

CORE_WATCHLIST = 'SOXL,SOXS,TQQQ,SQQQ,QQQ,SPY,SMH'
FALLBACK_UNIVERSE = (
    'SOXL,SOXS,TQQQ,SQQQ,QQQ,SPY,SMH,'
    'NVDA,AMD,AVGO,MU,ARM,TSM,ASML,INTC,QCOM,'
    'AAPL,MSFT,AMZN,GOOGL,META,TSLA,PLTR,NFLX,COIN,MSTR,'
    'CRWD,APP,ORCL,DELL,HOOD,RKLB'
)

@dataclass
class Settings:
    app_key: str = os.getenv('KIWOOM_APP_KEY', '')
    app_secret: str = os.getenv('KIWOOM_APP_SECRET', '')
    db_path: str = os.getenv('DAYTRADER_DB', '/home/ubuntu/day-trader-api/daytrader.db')
    host: str = os.getenv('DAYTRADER_HOST', '0.0.0.0')
    port: int = int(os.getenv('DAYTRADER_PORT', '8000'))
    core_symbols: list[str] = field(default_factory=lambda: _symbols(os.getenv('DAYTRADER_CORE_SYMBOLS', CORE_WATCHLIST)))
    symbols: list[str] = field(default_factory=lambda: _symbols(os.getenv('DAYTRADER_SYMBOLS', FALLBACK_UNIVERSE)))
    rest_base: str = os.getenv('KIWOOM_REST_BASE', 'https://api.kiwoom.com')
    ws_url: str = os.getenv('KIWOOM_WS_URL', 'wss://api.kiwoom.com:10000/api/us/websocket')
    poll_seconds: float = float(os.getenv('DAYTRADER_POLL_SECONDS', '12'))
    daily_refresh_seconds: float = float(os.getenv('DAYTRADER_DAILY_REFRESH_SECONDS', '3600'))
    discovery_seconds: float = float(os.getenv('DAYTRADER_DISCOVERY_SECONDS', '600'))
    discovery_limit: int = int(os.getenv('DAYTRADER_DISCOVERY_LIMIT', '35'))
    discovery_min_price: float = float(os.getenv('DAYTRADER_DISCOVERY_MIN_PRICE', '5'))
    discovery_min_dollar: float = float(os.getenv('DAYTRADER_DISCOVERY_MIN_DOLLAR', '20000000'))

    def exchange_for(self, symbol: str) -> str:
        overrides = {
            'SOXL':'NY','SOXS':'NY','SPY':'NY','IWM':'NY','DIA':'NY',
            'SMH':'ND','NVDA':'ND','TQQQ':'ND','SQQQ':'ND','QQQ':'ND','PLTR':'ND','AMD':'ND','AAPL':'ND','MSFT':'ND',
            'MU':'ND','AMZN':'ND','GOOGL':'ND','TSLA':'ND','AVGO':'ND','ARM':'ND','ASML':'ND','INTC':'ND',
            'QCOM':'ND','META':'ND','NFLX':'ND','COIN':'ND','MSTR':'ND','CRWD':'ND','APP':'ND','ORCL':'NY',
            'DELL':'NY','HOOD':'ND','RKLB':'ND','TSM':'NY'
        }
        return overrides.get(symbol.upper(), 'ND')
