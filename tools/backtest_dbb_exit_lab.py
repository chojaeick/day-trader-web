from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path

import pandas as pd

from live_server.double_bollinger_v22 import DoubleBollingerV22Config, DoubleBollingerV22ExitPolicy
from tools.backtest_dbb_kr_v2_v21_v22 import FORCE_FLAT_MINUTE, NO_ENTRY_MINUTE, Pos, close_trade, load_data, summary
from tools.backtest_dbb_kr_v2_v21_v22_adaptive import build_frames_cached

OUT_DIR = Path('/home/ubuntu/day-trader-api')


def build_events(frames):
    by_time = {}
    cols = ['time','open','high','low','close','score','stage','base_entry','regime','inner_lower','mid']
    for sym, frame in frames.items():
        for r in frame[cols].to_dict('records'):
            r['symbol'] = sym
            by_time.setdefault(r['time'], []).append(r)
    out = []
    for ts in sorted(by_time):
        rows = by_time[ts]
        out.append((ts, ts.hour * 60 + ts.minute, {r['symbol']: r for r in rows}, [r for r in rows if bool(r['base_entry'])]))
    return out


def structural_hit(mode: str, row: dict) -> bool:
    p = float(row['close'])
    hi = float(row['high'])
    il = float(row['inner_lower'])
    mid = float(row['mid'])
    if mode == 'FULL_BELOW_INNER_LOWER':
        return hi < il
    if mode == 'CLOSE_BELOW_INNER_LOWER':
        return p < il
    if mode == 'CLOSE_BELOW_MID':
        return p < mid
    raise ValueError(mode)


def simulate(events, frames, *, min_score: float = 65.0, min_risk_pct: float = 0.010,
             max_risk_pct: float = 0.020, tp1_r: float = 2.0,
             partial_fraction: float = 0.50, structural_mode: str = 'FULL_BELOW_INNER_LOWER',
             runner_trail_pct: float = 0.0, breakeven_after_tp1: bool = False):
    cfg = DoubleBollingerV22Config(min_risk_pct=min_risk_pct, max_risk_pct=max_risk_pct,
                                   tp1_r_multiple=tp1_r, partial_fraction=partial_fraction)
    policy = DoubleBollingerV22ExitPolicy(cfg)
    pos = None
    trades = []
    risk_pct = tp1 = None
    mfe_pct = 0.0

    for ts, minute, row_by_symbol, base_candidates in events:
        if pos is not None:
            r = row_by_symbol.get(pos.symbol)
            if r is not None:
                p, hi, lo = float(r['close']), float(r['high']), float(r['low'])
                pos.high_watermark = max(pos.high_watermark, hi)
                mfe_pct = max(mfe_pct, (pos.high_watermark / pos.entry_price - 1.0) * 100.0)

                def finish(reason, px):
                    nonlocal pos
                    t = close_trade(pos, reason, px, ts)
                    t['risk_pct'] = risk_pct * 100.0
                    t['tp1_price'] = tp1
                    t['mfe_pct'] = mfe_pct
                    t['giveback_from_peak_pct'] = mfe_pct - t['pnl_pct']
                    trades.append(t)
                    pos = None

                if minute >= FORCE_FLAT_MINUTE:
                    finish('SESSION_FORCE_FLAT', p)
                    continue

                stop = pos.entry_price if (breakeven_after_tp1 and pos.partial_done) else pos.stop
                if lo <= stop:
                    finish('BREAKEVEN_STOP_AFTER_TP1' if (breakeven_after_tp1 and pos.partial_done) else 'ADAPTIVE_HARD_STOP', stop)
                    continue

                if partial_fraction > 0.0 and not pos.partial_done and hi >= tp1:
                    pos.realized_pct += partial_fraction * (tp1 / pos.entry_price - 1.0)
                    pos.remaining_fraction = 1.0 - partial_fraction
                    pos.partial_done = True
                    if partial_fraction >= 0.999999:
                        finish('TP1_FULL_EXIT', tp1)
                        continue

                if pos.partial_done and runner_trail_pct > 0.0:
                    trail_px = pos.high_watermark * (1.0 - runner_trail_pct)
                    if lo <= trail_px:
                        finish('RUNNER_HIGH_WATER_TRAIL', trail_px)
                        continue

                if structural_hit(structural_mode, r):
                    finish('STRUCTURAL_' + structural_mode, p)
                    continue

        if pos is None and minute < NO_ENTRY_MINUTE:
            cand = [r for r in base_candidates if float(r['score']) >= min_score]
            if cand:
                r = max(cand, key=lambda x: float(x['score']))
                price = float(r['close'])
                il = float(r['inner_lower'])
                risk_pct = policy.structural_risk_pct(price, il)
                tp1 = policy.tp1_price(price, il)
                pos = Pos(str(r['symbol']), ts, price, policy.initial_stop(price, il),
                          float(r['score']), str(r['stage']), str(r['regime']), price)
                mfe_pct = 0.0

    if pos is not None:
        f = frames[pos.symbol]
        r = f.iloc[-1]
        t = close_trade(pos, 'END_OF_DATA', float(r['close']), r['time'])
        t['risk_pct'] = risk_pct * 100.0
        t['tp1_price'] = tp1
        t['mfe_pct'] = mfe_pct
        t['giveback_from_peak_pct'] = mfe_pct - t['pnl_pct']
        trades.append(t)
    return pd.DataFrame(trades)


def extra_metrics(t: pd.DataFrame) -> dict:
    if t.empty:
        return {'median_mfe':0.0,'avg_mfe':0.0,'avg_giveback':0.0,'winner_avg_giveback':0.0}
    winners = t[t.pnl_pct > 0]
    return {
        'median_mfe': round(float(t.mfe_pct.median()),4),
        'avg_mfe': round(float(t.mfe_pct.mean()),4),
        'avg_giveback': round(float(t.giveback_from_peak_pct.mean()),4),
        'winner_avg_giveback': round(float(winners.giveback_from_peak_pct.mean()),4) if not winners.empty else 0.0,
    }


def grid():
    # Exit-only experiment. Entry is frozen: BASE, score>=65, no regime block.
    # Risk 1.0-2.0% is frozen from the current best neighborhood so this run isolates selling logic.
    return itertools.product(
        [1.5, 2.0, 2.5, 3.0, 3.5],
        [0.0, 0.25, 0.50, 0.75, 1.0],
        ['FULL_BELOW_INNER_LOWER', 'CLOSE_BELOW_INNER_LOWER', 'CLOSE_BELOW_MID'],
        [0.0, 0.01, 0.015, 0.02, 0.03],
        [False, True],
    )


def parse_args():
    p = argparse.ArgumentParser(description='DBB exit-only profit-maximization lab')
    p.add_argument('--workers', type=int, default=2)
    p.add_argument('--top', type=int, default=30)
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.perf_counter()
    raw = load_data()
    frames = build_frames_cached(raw, workers=args.workers, rebuild=False)
    events = build_events(frames)
    combos = list(grid())
    print(f'[EXIT LAB] symbols={len(frames)} events={len(events)} combinations={len(combos)}', flush=True)
    print('[ENTRY FROZEN] BASE score>=65 risk=1.0-2.0% no FALLING block', flush=True)

    board = []
    best = None
    best_key = None
    for i, (tp1_r, part, smode, trail, be) in enumerate(combos, 1):
        # Trail / BE have no meaning when there is no partial trigger.
        if part == 0.0 and (trail > 0.0 or be):
            continue
        c0 = time.perf_counter()
        t = simulate(events, frames, tp1_r=tp1_r, partial_fraction=part,
                     structural_mode=smode, runner_trail_pct=trail,
                     breakeven_after_tp1=be)
        s = summary(f'EXIT_{i:04d}', t)
        s.update({'tp1_r':tp1_r,'partial_fraction':part,'structural_mode':smode,
                  'runner_trail_pct':trail*100.0,'breakeven_after_tp1':be})
        s.update(extra_metrics(t))
        # Keep ranking transparent: expectancy first, then PF, then gross.
        board.append(s)
        key = (float(s['avg_pct']), float(s['pf']), float(s['gross_pct']))
        if best_key is None or key > best_key:
            best_key, best = key, t.copy()
        if i % 50 == 0 or i == len(combos):
            print(f'[{i:04d}/{len(combos)}] tested latest avg={s["avg_pct"]:+.4f}% gross={s["gross_pct"]:+.4f}% pf={s["pf"]:.3f} sec={time.perf_counter()-c0:.2f}', flush=True)

    df = pd.DataFrame(board).sort_values(['avg_pct','pf','gross_pct'], ascending=[False,False,False]).reset_index(drop=True)
    df.insert(0, 'rank', range(1, len(df)+1))
    out = OUT_DIR / 'dbb_exit_lab.csv'
    df.to_csv(out, index=False)
    if best is not None:
        best.to_csv(OUT_DIR / 'dbb_exit_lab_best_trades.csv', index=False)

    cols = ['rank','tp1_r','partial_fraction','structural_mode','runner_trail_pct','breakeven_after_tp1',
            'trades','win_rate','avg_pct','avg_win_pct','avg_loss_pct','gross_pct','pf','max_loss_pct',
            'partial_rate','median_mfe','avg_mfe','avg_giveback','winner_avg_giveback']
    print('\n=== TOP EXIT STRATEGIES ===')
    print(df[cols].head(args.top).to_string(index=False))

    # Direct control rows answer the central question: is partial selling clipping profits?
    controls = df[(df.structural_mode=='FULL_BELOW_INNER_LOWER') & (df.runner_trail_pct==0.0) & (df.breakeven_after_tp1==False)]
    print('\n=== CURRENT-STYLE STRUCTURAL EXIT: PARTIAL/TP1 CONTROL ===')
    print(controls[cols].sort_values(['tp1_r','partial_fraction']).to_string(index=False))
    print(f'\n[TIMING] total={time.perf_counter()-t0:.2f}s')
    print(f'CSV saved: {out}')


if __name__ == '__main__':
    main()
