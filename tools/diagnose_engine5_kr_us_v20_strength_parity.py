from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

import tools.backtest_dbb_engine5_fast_tuner_v4 as base
import tools.backtest_dbb_engine5_fast_tuner_v8 as v8
import tools.backtest_dbb_engine5_fast_tuner_v10 as v10
import tools.backtest_dbb_engine5_v16_wait_reaccel as v16
import tools.backtest_engine5_v17b_breakout_v16_veto as v17b
import tools.validate_engine5_v17c_opening_5m_hwm_sweep as sweep
import tools.validate_engine5_v17c_5m_context_1m_trigger as h
import tools.validate_engine5_v20_macd_strength as ms
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

CORE=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache/us_kr_mapped_core.pkl')
RAW_MIN=52.0
REL_MIN=1.45


def n(x): return str(x).zfill(6)

def build_kr():
    raw=load_data(); cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    frames0=base.build_cfg_frames(raw,cfg)
    f10={n(s):v10._refine_entry_frame(f) for s,f in frames0.items()}
    scored={n(s):f for s,f in reweight(f10,cfg,0.0).items()}
    strength={s:ms.add_strength(f) for s,f in scored.items()}
    raw_entries=v8.pack_entry_events(scored); ev10=sweep.filt_open(raw_entries); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={n(s):h.build_micro(b,cfg) for s,b in raw.items()}; ev18,_=h.build_veto_stream(ev17,micros)
    return scored,strength,ev18


def build_us():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    if int(d.get('time_shift_minutes',999))!=0: raise SystemExit('US cache is not original ET')
    raw=d['raw']; cfg=d['cfg']; scored=d['scored']; strength=d['strength']; micros=d['micros']
    # US session equivalent of KR opening veto: 09:40 ET onward.
    raw_entries=v8.pack_entry_events(scored)
    ev10={ts:rows for ts,rows in raw_entries.items() if pd.Timestamp(ts).hour*60+pd.Timestamp(ts).minute>=9*60+40}
    ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    return scored,strength,ev18


def event_rows(scored,strength,ev18,market):
    rows=[]
    for ts,cs in ev18.items():
        for c in cs:
            sym=n(c[0]); sf=strength[sym]; q=sf[sf.time<=pd.Timestamp(ts)]
            if q.empty: continue
            r=q.iloc[-1]; close=float(r.close); raw=float(r.macd_strength_raw) if pd.notna(r.macd_strength_raw) else np.nan; rel=float(r.macd_strength_rel) if pd.notna(r.macd_strength_rel) else np.nan
            rows.append(dict(market=market,symbol=sym,time=pd.Timestamp(ts),close=close,raw=raw,rel=rel,raw_bps=(raw/close*10000 if close else np.nan),keep=bool(np.isfinite(raw) and raw>=RAW_MIN and np.isfinite(rel) and rel>=REL_MIN)))
    return pd.DataFrame(rows)


def summarize(x):
    q=x[['raw','rel','raw_bps']].replace([np.inf,-np.inf],np.nan)
    pct=q.quantile([.1,.25,.5,.75,.9]).T
    print(f"{x.market.iloc[0]}: V18_events={len(x)} V20_keep={int(x.keep.sum())} keep_rate={x.keep.mean()*100:.2f}%")
    print('  medians raw={:.3f} rel={:.3f} raw_bps={:.4f}'.format(q.raw.median(),q.rel.median(),q.raw_bps.median()))
    print('  p75     raw={:.3f} rel={:.3f} raw_bps={:.4f}'.format(q.raw.quantile(.75),q.rel.quantile(.75),q.raw_bps.quantile(.75)))
    print('  p90     raw={:.3f} rel={:.3f} raw_bps={:.4f}'.format(q.raw.quantile(.90),q.rel.quantile(.90),q.raw_bps.quantile(.90)))
    return pct


def main():
    print('=== KR vs US V20 STRENGTH PARITY ===')
    ks,kst,ke=build_kr(); us,ust,ue=build_us()
    k=event_rows(ks,kst,ke,'KR'); u=event_rows(us,ust,ue,'US')
    summarize(k); summarize(u)
    print('\n=== SAME RAW52/REL1.45 GATE ===')
    print(f"KR keep {int(k.keep.sum())}/{len(k)} = {k.keep.mean()*100:.2f}%")
    print(f"US keep {int(u.keep.sum())}/{len(u)} = {u.keep.mean()*100:.2f}%")
    print('\n=== NOMINAL PRICE / RAW NORMALIZATION ===')
    print(f"KR median close={k.close.median():.2f} | US(KRW-equiv) median close={u.close.median():.2f}")
    print(f"KR median RAW/close={k.raw_bps.median():.4f} bps | US={u.raw_bps.median():.4f} bps")
    out=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache/kr_us_v20_strength_parity.csv')
    pd.concat([k,u],ignore_index=True).to_csv(out,index=False)
    print('WROTE',out)

if __name__=='__main__': main()
