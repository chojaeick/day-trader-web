from __future__ import annotations
import os
from dataclasses import dataclass, field


def _symbols(raw: str) -> list[str]:
    return [x.strip().upper() for x in raw.split(',') if x.strip()]

DEFAULT_UNIVERSE = (
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
    symbols: list[str] = field(default_factory=lambda: _symbols(os.getenv('DAYTRADER_SYMBOLS', DEFAULT_UNIVERSE)))
    rest_base: str = os.getenv('KIWOOM_REST_BASE', 'https://api.kiwoom.com')
    ws_url: str = os.getenv('KIWOOM_WS_URL', 'wss://api.kiwoom.com:10000/api/us/websocket')
    poll_seconds: float = float(os.getenv('DAYTRADER_POLL_SECONDS', '12'))
    daily_refresh_seconds: float = float(os.getenv('DAYTRADER_DAILY_REFRESH_SECONDS', '3600'))

    def exchange_for(self, symbol: str) -> str:
        overrides = {
            # NYSE/Arca family (verified SOXL=NY during setup; common NY/Arca ETFs use NY in Kiwoom)
            'SOXL':'NY','SOXS':'NY','SPY':'NY','SMH':'NY','IWM':'NY','DIA':'NY',
            # Nasdaq
            'NVDA':'ND','TQQQ':'ND','SQQQ':'ND','QQQ':'ND','PLTR':'ND','AMD':'ND','AAPL':'ND','MSFT':'ND',
            'MU':'ND','AMZN':'ND','GOOGL':'ND','TSLA':'ND','AVGO':'ND','ARM':'ND','ASML':'ND','INTC':'ND',
            'QCOM':'ND','META':'ND','NFLX':'ND','COIN':'ND','MSTR':'ND','CRWD':'ND','APP':'ND','ORCL':'ND',
            'DELL':'NY','HOOD':'ND','RKLB':'ND','TSM':'NY'
        }
        return overrides.get(symbol.upper(), 'ND')
