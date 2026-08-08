import numpy as np
import pandas as pd


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df['high'] + df['low'] + df['close']) / 3
    pv = typical * df['volume']
    return pv.cumsum() / df['volume'].cumsum().replace(0, np.nan)


def enrich_intraday(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['ema9'] = ema(out['close'], 9)
    out['ema20'] = ema(out['close'], 20)
    out['ema50'] = ema(out['close'], 50)
    out['rsi14'] = rsi(out['close'])
    out['atr14'] = atr(out)
    out['vwap'] = vwap(out)
    out['vol_ma20'] = out['volume'].rolling(20, min_periods=5).mean()
    out['rvol'] = out['volume'] / out['vol_ma20'].replace(0, np.nan)
    out['prev_high20'] = out['high'].rolling(20, min_periods=5).max().shift(1)
    return out
