from __future__ import annotations

import pandas as pd

from live_server.double_bollinger_engine5 import DoubleBollingerEngine5
from tools.backtest_dbb_engine5_tuner import load_data, to_5m

TARGET_SYMBOL = '484810'
TARGET_TIME = pd.Timestamp('2026-08-10 11:15:00+09:00')


def main():
    raw = load_data()
    bars = raw[TARGET_SYMBOL]
    f = DoubleBollingerEngine5().enrich(to_5m(bars))
    r = f.loc[pd.to_datetime(f['time']) == TARGET_TIME]
    if r.empty:
        raise SystemExit(f'target bar not found: {TARGET_SYMBOL} {TARGET_TIME}')
    x = r.iloc[0]
    old_signal = bool(x['trend_up'] and x['entry_score'] >= 70.0)
    print('=== ENGINE 5 HARD-GATE FIX CHECK ===')
    print(f'symbol={TARGET_SYMBOL} time={TARGET_TIME}')
    print(f'close={float(x.close):.2f} entry_score={float(x.entry_score):.2f}')
    print(f'trend_up={bool(x.trend_up)} mid_slope8={float(x.mid_slope8):.4f}')
    print(f'macd={float(x.macd):.4f} signal={float(x.macd_signal):.4f} macd_above_signal={bool(x.macd_above_signal)} golden={bool(x.macd_golden_cross)}')
    print(f'macd_slope={float(x.macd_slope):+.4f} signal_slope={float(x.macd_signal_slope):+.4f} spread={float(x.macd_slope_spread):+.4f}')
    print(f'rsi={float(x.rsi):.2f} rsi_slope={float(x.rsi_slope):+.3f} rsi_accel={float(x.rsi_accel):+.3f}')
    print('--- HARD GATES ---')
    print(f'gate_trend_up={bool(x.gate_trend_up)}')
    print(f'gate_macd_rising={bool(x.gate_macd_rising)}')
    print(f'gate_macd_accel={bool(x.gate_macd_accel)}')
    print(f'gate_rsi_rising={bool(x.gate_rsi_rising)}')
    print(f'entry_gate={bool(x.entry_gate)}')
    print(f'OLD_LOGIC_SIGNAL={old_signal}')
    print(f'CORRECTED_SIGNAL={bool(x.entry_signal)}')
    print('EXPECTED: OLD_LOGIC_SIGNAL may be True, but CORRECTED_SIGNAL must be False if MACD itself was not rising.')


if __name__ == '__main__':
    main()
