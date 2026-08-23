from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

@app.get('/api/v5/momentum-cache/USA')
def v44_momentum_cache_usa():
    import time as _time
    rows=[]
    now=_time.time()
    cache=getattr(k,'_momentum_daily_cache',{}) or {}
    for key,feat in cache.items():
        try:
            symbol,exchange=key
        except Exception:
            continue
        feat=feat or {}
        age=now-float(feat.get('_cached_at',0) or 0)
        if age>1800:
            continue
        rows.append({
            'symbol':symbol,'exchange':exchange,
            'ok':feat.get('ok'),'reason':feat.get('reason'),
            'daily_rows':feat.get('daily_rows'),'pages':feat.get('pages'),
            'macd':feat.get('macd'),
            'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),
            'macd_cross_5':bool(feat.get('macd_cross_5')),
            'high_52w_gap_pct':feat.get('high_52w_gap_pct'),
            'near_52w_high':bool(feat.get('near_52w_high')),
            'momentum_match':bool(feat.get('momentum_match')),
            'age_sec':round(age,1),
        })
    rows.sort(key=lambda r:(0 if r.get('momentum_match') else 1, r.get('symbol') or ''))
    ok=[r for r in rows if r.get('ok')]
    return {
        'ok':True,
        'cursor':getattr(k,'_v43_momentum_cursor',None),
        'cached_count':len(rows),
        'feature_ok_count':len(ok),
        'feature_fail_count':len(rows)-len(ok),
        'macd_cross_5_count':sum(1 for r in ok if r.get('macd_cross_5')),
        'near_52w_count':sum(1 for r in ok if r.get('near_52w_high')),
        'momentum_match_count':sum(1 for r in ok if r.get('momentum_match')),
        'formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)',
        'rows':rows,
    }
'''

def main():
    s=API.read_text()
    if '/api/v5/momentum-cache/USA' not in s:
        anchor='app = FastAPI'
        idx=s.find(anchor)
        if idx<0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        # insert after the complete FastAPI(...) construction line/block by finding first newline after it
        # api.py uses a single app assignment statement in the current branch.
        line_end=s.find('\n',idx)
        if line_end<0:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: app line end')
        s=s[:line_end+1]+PATCH+s[line_end+1:]
        API.write_text(s)
    print('FINDER_MOMENTUM_CACHE_DIAG_V44_OK')

if __name__=='__main__':
    main()
