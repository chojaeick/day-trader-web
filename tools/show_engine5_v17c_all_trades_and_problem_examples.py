from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.diagnose_engine5_v17c_extra_entry_failures as diag
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

THRESHOLD = 50
OUT_DIR = Path('/home/ubuntu/day-trader-api/engine5_v16_full_validation')


def main():
    raw = load_data()
    base_cfg = DoubleBollingerEngine5Config()
    cfg = replace(base_cfg, macd_slope_spread_full_ratio=2.0, rsi_slope_full_ratio=1.5)

    packed = v8.base.pack_exit_events(raw, base_cfg)
    states = base.pack_state_events(base.build_cfg_frames(raw, base_cfg))
    frames = base.build_cfg_frames(raw, cfg)
    f10 = {s: v10._refine_entry_frame(f) for s, f in frames.items()}
    scored = reweight(f10, cfg, 0.0)
    ev10 = diag.filt_open(v8.pack_entry_events(scored))
    ev16, waits = v16.build_wait_events(ev10, raw, cfg, False)
    ev_v17c, added, skipped = v17b.build_v17b(ev16, scored, waits)
    t = diag.key_df(multi.simulate_multi(packed, ev_v17c, states, THRESHOLD)).sort_values('entry_time').reset_index(drop=True)

    print('=== V17C ALL REALIZED TRADES ===')
    print('TOTAL=', len(t), 'WINS=', int((t.pnl_pct > 0).sum()), 'LOSSES=', int((t.pnl_pct <= 0).sum()), 'GROSS=', round(float(t.pnl_pct.sum()), 6))
    print('Columns: no | symbol | entry | exit | pnl% | reason')
    basic = t[['symbol','entry_time','exit_time','pnl_pct','reason']].copy()
    basic.insert(0, 'no', range(1, len(basic) + 1))
    print(basic.to_string(index=False))

    print('\n=== WORST 25 TRADES ===')
    worst = t.nsmallest(25, 'pnl_pct').copy()
    print(worst[['symbol','entry_time','exit_time','entry_price','exit_price','pnl_pct','reason']].to_string(index=False))

    print('\n=== MOST FREQUENT LOSS SYMBOLS ===')
    loss = t[t.pnl_pct <= 0].copy()
    bysym = loss.groupby('symbol').agg(
        losses=('pnl_pct','size'),
        gross_loss=('pnl_pct','sum'),
        avg_loss=('pnl_pct','mean'),
        worst=('pnl_pct','min'),
    ).sort_values(['losses','gross_loss'], ascending=[False, True]).head(20)
    print(bysym.to_string())

    print('\n=== WORST LOSS SYMBOLS BY TOTAL DAMAGE ===')
    damage = loss.groupby('symbol').agg(
        losses=('pnl_pct','size'),
        gross_loss=('pnl_pct','sum'),
        avg_loss=('pnl_pct','mean'),
        worst=('pnl_pct','min'),
    ).sort_values('gross_loss').head(20)
    print(damage.to_string())

    # Enrich the worst 12 so the user can inspect concrete market context without drowning in features.
    evmap = diag.event_lookup(ev_v17c)
    rich = {s: v16.build_rich_micro(raw[s], cfg) for s in raw}
    import tools.backtest_dbb_engine5_v15_boundary as v15
    micro15 = {s: v15.build_1m_micro(raw[s], cfg) for s in raw}
    gaps = {s: v15.daily_gap_map(raw[s]) for s in raw}

    detail = []
    for _, r in t.nsmallest(12, 'pnl_pct').iterrows():
        sym = str(r.symbol).zfill(6)
        key = (sym, str(pd.Timestamp(r.entry_time)))
        detail.append(diag.enrich_trade(r, evmap.get(key), scored[sym], rich[sym], micro15[sym], gaps[sym], raw[sym]))
    d = pd.DataFrame(detail)
    cols = [
        'symbol','entry_time','pnl_pct','reason','score','gap_pct','would_v15_wait',
        'down_steps','fade_ratio','step_ratio','macd_slope_1m','spread_1m','rsi_slope_1m',
        'mfe_1m_pct','mae_1m_pct','mfe_3m_pct','mae_3m_pct','mfe_5m_pct','mae_5m_pct',
        'mfe_10m_pct','mae_10m_pct'
    ]
    cols = [c for c in cols if c in d.columns]
    print('\n=== WORST 12 — MARKET CONTEXT ===')
    print(d[cols].to_string(index=False))

    # Export for easier sorting/manual review.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_csv = OUT_DIR / 'v17c_all_286_trades_manual_review.csv'
    worst_csv = OUT_DIR / 'v17c_worst12_market_context.csv'
    t.to_csv(all_csv, index=False)
    d.to_csv(worst_csv, index=False)
    print('\n[ALL CSV]', all_csv)
    print('[WORST CONTEXT CSV]', worst_csv)


if __name__ == '__main__':
    main()
