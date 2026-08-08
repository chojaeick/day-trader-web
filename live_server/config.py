from __future__ import annotations
import os
from dataclasses import dataclass, field


def _symbols(raw: str) -> list[str]:
    return [x.strip().upper() for x in raw.split(',') if x.strip()]

@dataclass
class Settings:
    app_key: str = os.getenv('KIWOOM_APP_KEY', '')
    app_secret: str = os.getenv('KIWOOM_APP_SECRET', '')
    db_path: str = os.getenv('DAYTRADER_DB', '/home/ubuntu/day-trader-api/daytrader.db')
    host: str = os.getenv('DAYTRADER_HOST', '0.0.0.0')
    port: int = int(os.getenv('DAYTRADER_PORT', '8000'))
    symbols: list[str] = field(default_factory=lambda: _symbols(os.getenv('DAYTRADER_SYMBOLS', 'SOXL,NVDA,TQQQ,SQQQ,PLTR,AMD,AAPL,MSFT,MU,AMZN')))
    rest_base: str = os.getenv('KIWOOM_REST_BASE', 'https://api.kiwoom.com')
    ws_url: str = os.getenv('KIWOOM_WS_URL', 'wss://api.kiwoom.com:10000/api/us/websocket')
    poll_seconds: float = float(os.getenv('DAYTRADER_POLL_SECONDS', '10'))

    def exchange_for(self, symbol: str) -> str:
        overrides = {
            'SOXL':'NY', 'SPY':'NY', 'DIA':'NY', 'IWM':'NY',
            'NVDA':'ND','TQQQ':'ND','SQQQ':'ND','PLTR':'ND','AMD':'ND','AAPL':'ND','MSFT':'ND','MU':'ND','AMZN':'ND','GOOGL':'ND','TSLA':'ND'
        }
        return overrides.get(symbol.upper(), 'ND')
