#!/usr/bin/env python3
"""DAY TRADER V124 - causal ENTRY quality + breakout-radar audit.

Purpose
-------
Compare today's Williams entries with a missed explosive mover (default: 413630 CP System)
using ONLY information available up to each candidate minute. Forward returns are computed
only afterwards for evaluation and never participate in the trigger.

This is OFFLINE DIAGNOSTIC ONLY:
- no orders
- no service restart
- no API calls
- reads /home/ubuntu/day-trader-api/daytrader.db

Default symbols include the current-day Williams examples seen during V118~V123 validation
plus 413630. You may pass explicit symbols after the date.

Usage:
  python3 tools/v124_entry_quality_breakout_audit.py
  python3 tools/v124_entry_quality_breakout_audit.py 20260826
  python3 tools/v124_entry_quality_breakout_audit.py 20260826 413630 950260 041190 047040
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB=Path('/home/ubuntu/day-trader-api/daytrader.db')
KST=timezone(timedelta(hours=9))
DEFAULT_SYMBOLS=['413630','950260','041190','047040','015760','010170','037440','096530']


def f(x, default=0.0):
    try:
        if x is None: return default
        return float(str(x).replace(',',''))
    except Exception:
        return default


def pct(a,b):
    return ((a/b)-1.0)*100.0 if a and b else None


def median(xs):
    ys=sorted(x for x in xs if x is not None)
    if not ys: return None
    n=len(ys)
    return ys[n//2] if n%2 else (ys[n//2-1]+ys[n//2])/2


def table_cols(con, table):
    try:
        return [r[1] for r in con.execute(f'pragma table_info({table})')]
    except Exception:
        return []


def tables(con):
    return [r[0] for r in con.execute("select name from sqlite_master where type='table'")]


def normalize_time(x):
    s=str(x or '').strip()
    digits=''.join(ch for ch in s if ch.isdigit())
    # YYYYMMDDHHMMSS[ffffff]
    if len(digits)>=12 and digits[:8].isdigit():
        return digits[:14].ljust(14,'0')
    return None


def load_minute_bars(con, sym, day):
    """Load 1m KOREA bars from any known local table, newest schema first."""
    candidates=['historical_minute_bars','korea_minute_bars','minute_bars']
    for t in candidates:
        if t not in tables(con):
            continue
        cols=table_cols(con,t)
        if not {'open','high','low','close'}.issubset(cols):
            continue
        symbol_col='symbol' if 'symbol' in cols else ('stk_cd' if 'stk_cd' in cols else None)
        if not symbol_col: continue
        time_col=next((c for c in ('et_time','time','ts','datetime','trade_time') if c in cols),None)
        if not time_col: continue
        vol_col='volume' if 'volume' in cols else ('vol' if 'vol' in cols else None)
        if not vol_col: continue
        date_col='trade_date' if 'trade_date' in cols else None
        q=f"select {time_col},open,high,low,close,{vol_col} from {t} where {symbol_col}=?"
        args=[sym]
        if date_col:
            q+=f" and {date_col}=?"
            args.append(day)
        q+=f" order by {time_col}"
        try:
            raw=con.execute(q,args).fetchall()
        except Exception:
            continue
        out=[]
        for tm,o,h,l,c,v in raw:
            nt=normalize_time(tm)
            if not nt or nt[:8]!=day: continue
            out.append({'time':nt,'open':f(o),'high':f(h),'low':f(l),'close':f(c),'volume':f(v)})
        if out:
            # de-dupe by minute; keep last row
            d={r['time'][:12]:r for r in out}
            return [d[k] for k in sorted(d)]
    return []


def load_tracker_bars(con,sym,day):
    """Fallback: reconstruct sparse price observations from tracker snapshots."""
    if 'v4_tracker_snapshots' not in tables(con): return []
    cols=table_cols(con,'v4_tracker_snapshots')
    if not {'market','symbol','payload_json'}.issubset(cols): return []
    time_col='ts' if 'ts' in cols else ('created_at' if 'created_at' in cols else None)
    if not time_col:return []
    try:
        rows=con.execute(
            f"select {time_col},payload_json from v4_tracker_snapshots where market='KOREA' and symbol=? order by {time_col}",(sym,)
        ).fetchall()
    except Exception:
        return []
    out=[]
    for tm,p in rows:
        nt=normalize_time(tm)
        if not nt or nt[:8]!=day: continue
        try:d=json.loads(p or '{}')
        except Exception:d={}
        price=f(d.get('price'))
        if price<=0:continue
        out.append({'time':nt,'open':price,'high':price,'low':price,'close':price,'volume':f(d.get('volume'))})
    d={r['time'][:12]:r for r in out}
    return [d[k] for k in sorted(d)]


def load_williams_signals(con,sym,day):
    """Find Williams entry telemetry from tracker snapshots, if available."""
    if 'v4_tracker_snapshots' not in tables(con): return []
    cols=table_cols(con,'v4_tracker_snapshots')
    time_col='ts' if 'ts' in cols else ('created_at' if 'created_at' in cols else None)
    if not time_col:return []
    try:
        rows=con.execute(
            f"select {time_col},payload_json from v4_tracker_snapshots where market='KOREA' and symbol=? order by {time_col}",(sym,)
        ).fetchall()
    except Exception:return []
    out=[]
    for tm,p in rows:
        nt=normalize_time(tm)
        if not nt or nt[:8]!=day:continue
        try:d=json.loads(p or '{}')
        except Exception:continue
        if bool(d.get('williams_entry')) or bool(d.get('williams_struct5_signal')):
            out.append((nt,f(d.get('price')),bool(d.get('williams_struct5_signal')),d.get('williams_struct5_reason')))
    return out


def ema(vals,n):
    if not vals:return None
    a=2/(n+1)
    e=vals[0]
    for x in vals[1:]:e=a*x+(1-a)*e
    return e


def feature_rows(bars):
    out=[]
    closes=[]; vols=[]
    for i,r in enumerate(bars):
        closes.append(r['close']);vols.append(r['volume'])
        if i<20:
            continue
        prior=bars[max(0,i-20):i]  # STRICTLY prior bars only
        pclose=closes[i-1]
        prior_high=max(x['high'] for x in prior)
        prior_low=min(x['low'] for x in prior)
        box_pct=((prior_high/prior_low)-1)*100 if prior_low else None
        vmed=median([x['volume'] for x in prior if x['volume']>0])
        vr=(r['volume']/vmed) if vmed and r['volume']>0 else None
        ret1=pct(r['close'],pclose)
        ret3=pct(r['close'],bars[i-3]['close']) if i>=3 else None
        ret5=pct(r['close'],bars[i-5]['close']) if i>=5 else None
        breakout_pct=pct(r['close'],prior_high)
        e5=ema(closes[max(0,i-20):i+1],5)
        e10=ema(closes[max(0,i-30):i+1],10)
        accel=bool(ret3 is not None and ret5 is not None and ret3>=1.2 and ret5>=1.8)
        vol_burst=bool(vr is not None and vr>=4.0)
        breakout=bool(breakout_pct is not None and breakout_pct>=0.25)
        compressed=bool(box_pct is not None and box_pct<=4.0)
        trend=bool(e5 and e10 and r['close']>e5>e10)
        # Radar score intentionally simple and causal. Threshold 70 is audit-only.
        score=0
        score += 30 if vol_burst else (18 if vr is not None and vr>=2.5 else 0)
        score += 25 if breakout else (12 if breakout_pct is not None and breakout_pct>0 else 0)
        score += 20 if accel else (10 if ret3 is not None and ret3>=0.8 else 0)
        score += 15 if compressed else 0
        score += 10 if trend else 0
        trigger=bool(score>=70 and breakout and (vol_burst or accel))
        out.append({
            **r,'idx':i,'vol_ratio':vr,'ret1':ret1,'ret3':ret3,'ret5':ret5,
            'prior20_high':prior_high,'box20_pct':box_pct,'breakout_pct':breakout_pct,
            'ema5':e5,'ema10':e10,'radar_score':score,'radar_trigger':trigger,
        })
    return out


def forward_eval(rows,bars):
    for z in rows:
        i=z['idx']; entry=z['close']
        for n in (5,10,20,30):
            fut=bars[i+1:min(len(bars),i+n+1)]
            if not fut:
                z[f'fwd{n}']=None;z[f'mfe{n}']=None;z[f'mae{n}']=None;continue
            last=fut[-1]['close']
            z[f'fwd{n}']=pct(last,entry)
            z[f'mfe{n}']=pct(max(x['high'] for x in fut),entry)
            z[f'mae{n}']=pct(min(x['low'] for x in fut),entry)
    return rows


def fmt(x,n=2):
    return '-' if x is None or (isinstance(x,float) and math.isnan(x)) else f'{x:.{n}f}'


def kst_hm(t):
    return t[8:10]+':'+t[10:12] if t and len(t)>=12 else '-'


def main():
    day=sys.argv[1] if len(sys.argv)>=2 and sys.argv[1].isdigit() and len(sys.argv[1])==8 else datetime.now(KST).strftime('%Y%m%d')
    arg0=2 if len(sys.argv)>=2 and sys.argv[1].isdigit() and len(sys.argv[1])==8 else 1
    syms=[str(x).replace('A','').zfill(6) for x in sys.argv[arg0:]] or DEFAULT_SYMBOLS
    if not DB.exists(): raise SystemExit(f'MISSING_DB {DB}')
    con=sqlite3.connect(str(DB),timeout=5)
    print('=== V124 ENTRY QUALITY + BREAKOUT RADAR CAUSAL AUDIT ===')
    print('DATE',day,'DB',DB)
    print('SYMBOLS',','.join(syms))
    print('RADAR_RULE score>=70 + breakout + (volume_burst or acceleration)')
    print('CAUSAL=YES (features use current/prior bars only; forward metrics are evaluation only)')

    summary=[]
    for sym in syms:
        bars=load_minute_bars(con,sym,day)
        source='minute_bars'
        if not bars:
            bars=load_tracker_bars(con,sym,day);source='tracker_sparse'
        sigs=load_williams_signals(con,sym,day)
        feats=forward_eval(feature_rows(bars),bars) if bars else []
        triggers=[x for x in feats if x['radar_trigger']]
        first=triggers[0] if triggers else None
        first_sig=sigs[0] if sigs else None
        print('\n---',sym,'---')
        print('SOURCE',source,'BARS',len(bars),'WILLIAMS_SIGNALS',len(sigs))
        if first_sig:
            print('WILLIAMS_FIRST',kst_hm(first_sig[0]),'price=',first_sig[1],'struct5=',first_sig[2],'reason=',str(first_sig[3])[:140])
        else:
            print('WILLIAMS_FIRST NONE')
        if first:
            print('RADAR_FIRST',kst_hm(first['time']),'price=',first['close'],
                  'score=',first['radar_score'],'VR=',fmt(first['vol_ratio']),
                  'R3=',fmt(first['ret3']),'R5=',fmt(first['ret5']),
                  'BOX20=',fmt(first['box20_pct']),'BRK=',fmt(first['breakout_pct']))
            print('OUTCOME','FWD5=',fmt(first['fwd5']),'MFE10=',fmt(first['mfe10']),
                  'MAE10=',fmt(first['mae10']),'FWD20=',fmt(first['fwd20']),'MFE30=',fmt(first['mfe30']))
        else:
            print('RADAR_FIRST NONE')
        # Best causal candidate by score, useful when threshold misses.
        best=max(feats,key=lambda x:x['radar_score'],default=None)
        if best:
            print('RADAR_BEST',kst_hm(best['time']),'price=',best['close'],'score=',best['radar_score'],
                  'VR=',fmt(best['vol_ratio']),'R3=',fmt(best['ret3']),'R5=',fmt(best['ret5']),
                  'BRK=',fmt(best['breakout_pct']),'MFE10=',fmt(best.get('mfe10')))
        summary.append((sym,len(bars),source,first_sig,first,best))

    print('\n=== COMPACT SUMMARY ===')
    print('SYM     BARS SOURCE         W_FIRST  R_FIRST R_SCORE R_PRICE  R3%    VR    MFE10% MAE10%')
    for sym,n,source,sig,first,best in summary:
        rr=first or best
        print(f'{sym:6s} {n:4d} {source:14s} {kst_hm(sig[0]) if sig else "-":7s} '
              f'{kst_hm(first["time"]) if first else "-":7s} '
              f'{(rr["radar_score"] if rr else 0):7.0f} {(rr["close"] if rr else 0):7.0f} '
              f'{fmt(rr.get("ret3") if rr else None):>6s} {fmt(rr.get("vol_ratio") if rr else None):>6s} '
              f'{fmt(rr.get("mfe10") if rr else None):>7s} {fmt(rr.get("mae10") if rr else None):>6s}')

    print('\nINTERPRETATION')
    print('- 413630 should ideally trigger near the first volume/price expansion, not after the late-stage run.')
    print('- Losing Williams entries should show weaker R3/R5, volume ratio, breakout persistence, or compression-release quality.')
    print('- Do NOT deploy the radar threshold live from one day; use this audit to select features, then replay multiple days.')
    print('DONE')

if __name__=='__main__':
    main()
