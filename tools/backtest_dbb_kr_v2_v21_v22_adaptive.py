from __future__ import annotations

import pandas as pd

from live_server.double_bollinger_v22 import DoubleBollingerV22ExitPolicy
from tools.backtest_dbb_kr_v2_v21_v22 import (
    FORCE_FLAT_MINUTE,
    NO_ENTRY_MINUTE,
    Pos,
    build_events,
    close_trade,
    enrich,
    load_data,
    print_exit_reasons,
    print_regime,
    simulate_legacy,
    summary,
)

V22_POLICY = DoubleBollingerV22ExitPolicy()


def simulate_v22_adaptive(frames):
    """V2.1 entries + V2.2 adaptive stop / 2R TP1 / full-candle structural exit."""
    events = build_events(frames)
    pos = None
    trades = []
    entry_inner_lower = None
    tp1 = None
    risk_pct = None

    for ts in sorted(events):
        minute = ts.hour * 60 + ts.minute
        rows = events[ts]

        if pos is not None:
            match = next((r for s, r in rows if s == pos.symbol), None)
            if match is not None:
                p = float(match['close'])
                hi = float(match['high'])
                lo = float(match['low'])
                inner_lower = float(match['inner_lower'])
                pos.high_watermark = max(pos.high_watermark, hi)

                if minute >= FORCE_FLAT_MINUTE:
                    t = close_trade(pos, 'SESSION_FORCE_FLAT', p, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

                # Absolute loss cap: adaptive 1R, clamped to 0.8%..2.0%.
                if lo <= pos.stop:
                    t = close_trade(pos, 'V22_ADAPTIVE_HARD_STOP', pos.stop, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

                # TP1 = +2R; fill 50% at the target if the 1m high reaches it.
                if not pos.partial_done and hi >= tp1:
                    pos.realized_pct += 0.5 * (tp1 / pos.entry_price - 1.0)
                    pos.remaining_fraction = 0.5
                    pos.partial_done = True

                # Structural exit requires the ENTIRE completed 1m candle below
                # inner-lower: even the candle high must remain below the band.
                if V22_POLICY.candle_fully_below_inner_lower(hi, inner_lower):
                    reason = (
                        'V22_RUNNER_FULL_CANDLE_BELOW_INNER_LOWER'
                        if pos.partial_done
                        else 'V22_PRE_TP1_FULL_CANDLE_BELOW_INNER_LOWER'
                    )
                    t = close_trade(pos, reason, p, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    trades.append(t)
                    pos = None
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            candidates = [(s, r) for s, r in rows if bool(r['structure_entry'])]
            if candidates:
                sym, r = max(candidates, key=lambda z: float(z[1]['score']))
                price = float(r['close'])
                entry_inner_lower = float(r['inner_lower'])
                risk_pct = V22_POLICY.structural_risk_pct(price, entry_inner_lower)
                stop = V22_POLICY.initial_stop(price, entry_inner_lower)
                tp1 = V22_POLICY.tp1_price(price, entry_inner_lower)
                pos = Pos(
                    sym, ts, price, stop,
                    float(r['score']), str(r['stage']), str(r['regime']), price
                )

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        t = close_trade(pos, 'END_OF_DATA', float(r['close']), r['time'])
        t['risk_pct'] = risk_pct * 100.0
        t['tp1_price'] = tp1
        trades.append(t)

    return pd.DataFrame(trades)


def main():
    raw = load_data()
    print('KR symbols=', len(raw), 'bars=', sum(len(x) for x in raw.values()))

    frames = {}
    for i, (sym, bars) in enumerate(sorted(raw.items()), 1):
        print(f'[{i}/{len(raw)}] diagnostics {sym} bars={len(bars)}', flush=True)
        frames[sym] = enrich(sym, bars)

    base = simulate_legacy(frames, 'base_entry')
    struct = simulate_legacy(frames, 'structure_entry')
    v22 = simulate_v22_adaptive(frames)

    print('\n=== SUMMARY ===')
    print(pd.DataFrame([
        summary('V2_BASE', base),
        summary('V2.1_STRUCTURE', struct),
        summary('V2.2_ADAPTIVE_EXIT', v22),
    ]).to_string(index=False))

    print_regime('V2_BASE', base)
    print_regime('V2.1_STRUCTURE', struct)
    print_regime('V2.2_ADAPTIVE_EXIT', v22)
    print_exit_reasons('V2_BASE', base)
    print_exit_reasons('V2.1_STRUCTURE', struct)
    print_exit_reasons('V2.2_ADAPTIVE_EXIT', v22)

    base.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v2_base_trades_3way.csv', index=False)
    struct.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v21_structure_trades_3way.csv', index=False)
    v22.to_csv('/home/ubuntu/day-trader-api/dbb_kr_v22_adaptive_trades_3way.csv', index=False)
    print('\nCSV saved: dbb_kr_v2_base_trades_3way.csv, dbb_kr_v21_structure_trades_3way.csv, dbb_kr_v22_adaptive_trades_3way.csv')


if __name__ == '__main__':
    main()
