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
import tools.validate_engine5_v17c_multi_symbol as multi
import tools.validate_engine5_v20_macd_strength as ms
import tools.validate_engine5_integrated_full_history as integ
from live_server.double_bollinger_engine5 import DoubleBollingerEngine5Config
from tools.backtest_dbb_engine5_tuner import reweight
from tools.backtest_dbb_kr_v2_v21_v22 import load_data

ROOT=Path('/home/ubuntu/day-trader-api/engine5_us_kr_mapped_cache')
CORE=ROOT/'us_kr_mapped_core.pkl'
OUT=ROOT/'v20_norm_kr_us_behavior.csv'
RAW_BPS_MIN=11.166071
REL_MIN=1.45
THRESHOLD=50


def n(x): return str(x).zfill(6)

def f(x):
    try:
        y=float(x); return y if np.isfinite(y) else np.nan
    except Exception:return np.nan

def session_patch_us():
    base.NO_ENTRY_MINUTE=15*60+30; base.FORCE_FLAT_MINUTE=15*60+50
    sweep.OPEN_BUY_MINUTE=9*60+40; sweep.OPENING_ENTRY_END=10*60+30
    multi.OPEN_MINUTE=9*60+40

def build_kr():
    raw={n(k):v for k,v in load_data().items()}
    cfg0=DoubleBollingerEngine5Config(); cfg=replace(cfg0,macd_slope_spread_full_ratio=2.0,rsi_slope_full_ratio=1.5)
    packed=v8.base.pack_exit_events(raw,cfg0); states=base.pack_state_events(base.build_cfg_frames(raw,cfg0))
    frames0=base.build_cfg_frames(raw,cfg); f10={n(s):v10._refine_entry_frame(x) for s,x in frames0.items()}
    scored={n(s):x for s,x in reweight(f10,cfg,0.0).items()}; strength={s:ms.add_strength(x) for s,x in scored.items()}
    raw_entries=v8.pack_entry_events(scored); ev10=sweep.filt_open(raw_entries); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits)
    micros={n(s):h.build_micro(b,cfg) for s,b in raw.items()}; ev18,_=h.build_veto_stream(ev17,micros)
    return raw,packed,states,scored,strength,ev18

def build_us():
    with CORE.open('rb') as fh:d=pickle.load(fh)
    if int(d.get('time_shift_minutes',999))!=0:raise SystemExit('US cache must preserve original ET')
    session_patch_us()
    raw=d['raw']; packed=d['packed']; states=d['states']; scored=d['scored']; strength=d['strength']; cfg=d['cfg']; micros=d['micros']
    raw_entries=v8.pack_entry_events(scored); ev10=sweep.filt_open(raw_entries); ev16,waits=v16.build_wait_events(ev10,raw,cfg,False); ev17,_,_=v17b.build_v17b(ev16,scored,waits); ev18,_=h.build_veto_stream(ev17,micros)
    return raw,packed,states,scored,strength,ev18

def select_norm(scored,strength,ev18):
    out={}; meta=[]
    for ts,cs in sorted(ev18.items()):
        for c in cs:
            sym=n(c[0]); q=strength[sym]; q=q[q.time<=pd.Timestamp(ts)]
            if q.empty:continue
            r=q.iloc[-1]; close=f(r.close); raw=f(r.macd_strength_raw); rel=f(r.macd_strength_rel); bps=raw/close*10000 if close else np.nan
            above=bool(f(r.macd_gap)>0); keep=bool(np.isfinite(bps) and bps>=RAW_BPS_MIN and np.isfinite(rel) and rel>=REL_MIN and above)
            meta.append(dict(symbol=sym,time=pd.Timestamp(ts),raw_bps=bps,rel=rel,macd_gap=f(r.macd_gap),macd=f(r.macd),signal=f(r.macd_signal),rsi=f(r.rsi),rsi_slope=f(r.rsi_slope),macd_slope=f(r.macd_slope),gap_delta=f(r.macd_gap_delta),mid_slope8=f(r.mid_slope8),entry_score=f(r.entry_score),keep=keep))
            if keep:
                ext=integ.entry_extension_5m(scored,sym,ts)
                if pd.notna(ext) and ext>=integ.V20_EXTREME_CAP:continue
                out.setdefault(pd.Timestamp(ts),[]).append(c)
    return out,pd.DataFrame(meta)

def attach_trade_meta(market,tr,meta):
    if tr.empty:return pd.DataFrame()
    z=tr.copy(); z['market']=market; z['symbol']=z.symbol.astype(str).str.zfill(6); z['entry_time']=pd.to_datetime(z.entry_time)
    k=meta[meta.keep].copy(); k['symbol']=k.symbol.astype(str).str.zfill(6); k['entry_time']=pd.to_datetime(k.time)
    cols=['symbol','entry_time','raw_bps','rel','macd_gap','macd','signal','rsi','rsi_slope','macd_slope','gap_delta','mid_slope8','entry_score']
    z=z.merge(k.rename(columns={'time':'entry_time'})[cols],on=['symbol','entry_time'],how='left')
    z['gross_win']=pd.to_numeric(z.pnl_pct,errors='coerce')>0
    return z

def summary(name,z):
    p=pd.to_numeric(z.pnl_pct,errors='coerce')
    gp=p[p>0].sum(); gl=-p[p<0].sum()
    print(f'{name}: trades={len(z)} WR={(p>0).mean()*100:.2f}% gross={p.sum():+.4f}% avg={p.mean():+.4f}% PF={(gp/gl if gl>0 else np.inf):.3f}')
    print('  exits:',z.reason.value_counts().to_dict())
    if 'source' in z: print('  source:',z.source.value_counts().to_dict())
    print('  medians:',{c:round(float(pd.to_numeric(z[c],errors="coerce").median()),4) for c in ['raw_bps','rel','macd_gap','rsi','rsi_slope','macd_slope','mid_slope8','entry_score'] if c in z})

def main():
    print('=== V20 NORMALIZED TREND — KR vs US BEHAVIOR ===')
    kr=build_kr(); kev,kmeta=select_norm(kr[3],kr[4],kr[5]); ktr=multi.simulate_multi(kr[1],kev,kr[2],THRESHOLD); kz=attach_trade_meta('KR',ktr,kmeta)
    us=build_us(); uev,umeta=select_norm(us[3],us[4],us[5]); utr=multi.simulate_multi(us[1],uev,us[2],THRESHOLD); uz=attach_trade_meta('US',utr,umeta)
    summary('KR',kz); summary('US',uz)
    allz=pd.concat([kz,uz],ignore_index=True); allz.to_csv(OUT,index=False); print('WROTE',OUT)
    print('\n=== US PER SYMBOL ===')
    if len(uz):
        q=uz.copy(); q['win']=pd.to_numeric(q.pnl_pct,errors='coerce')>0
        print(q.groupby('symbol').agg(trades=('pnl_pct','size'),WR=('win','mean'),gross=('pnl_pct','sum'),avg=('pnl_pct','mean')).assign(WR=lambda d:d.WR*100).sort_values('gross').to_string())
    print('\n=== WORST US 10 ===')
    cols=[c for c in ['symbol','entry_time','exit_time','pnl_pct','reason','raw_bps','rel','macd_gap','rsi','rsi_slope','macd_slope','mid_slope8','entry_score'] if c in uz]
    print(uz.sort_values('pnl_pct').head(10)[cols].to_string(index=False))
    print('\n=== BEST US 10 ===')
    print(uz.sort_values('pnl_pct',ascending=False).head(10)[cols].to_string(index=False))

if __name__=='__main__':main()
