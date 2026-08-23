from pathlib import Path
import re

KIWOOM=Path('live_server/kiwoom.py')

NEW_FUNC=r'''
    def _v39_momentum_rank_candidates(self, volume_rows, dollar_rows):
        """V43: rate-safe rotating evaluation of the published momentum formula.

        Formula is unchanged:
        MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)

        Instead of evaluating every Top100 candidate on every discovery cycle,
        evaluate a bounded rotating batch and reuse the 15-minute daily cache.
        This avoids colliding with minute backfills and Kiwoom chart rate limits.
        """
        import time as _time

        pool={}
        def add(rows,label):
            for rank,x in enumerate((rows or [])[:100],1):
                sym=str(x.get('stk_cd') or '').upper().strip()
                if not sym:
                    continue
                rec=pool.setdefault(sym,{'symbol':sym,'raw':x,'volume_rank':9999,'dollar_rank':9999})
                rec[label+'_rank']=min(rec[label+'_rank'],rank)
                if not rec.get('raw'):
                    rec['raw']=x
        add(volume_rows,'volume')
        add(dollar_rows,'dollar')

        exmap={'1':'NY','2':'ND','3':'NA','NYSE':'NY','NASDAQ':'ND','AMEX':'NA','NY':'NY','ND':'ND','NA':'NA','AM':'NA'}
        candidates=[]
        for rec in pool.values():
            x=rec['raw']
            price=abs(num(x.get('cur_prc')))
            if price < float(self.s.discovery_min_price):
                continue
            rawex=str(x.get('stex_tp') or '').upper().strip()
            rec['exchange']=exmap.get(rawex) or self.active_exchange(rec['symbol'])
            rec['rank_key']=min(rec['volume_rank'],rec['dollar_rank'])
            candidates.append(rec)

        candidates.sort(key=lambda r:(r['rank_key'],r['symbol']))
        if not candidates:
            return []

        # Evaluate at most 8 new/stale symbols per cycle. The cursor guarantees
        # all Top100 liquidity candidates are eventually covered without burst load.
        if not hasattr(self,'_v43_momentum_cursor'):
            self._v43_momentum_cursor=0
        batch_size=min(8,len(candidates))
        start=int(self._v43_momentum_cursor)%len(candidates)
        batch=[candidates[(start+i)%len(candidates)] for i in range(batch_size)]
        self._v43_momentum_cursor=(start+batch_size)%len(candidates)

        for rec in batch:
            try:
                self._v39_daily_momentum_feature(rec['symbol'],rec['exchange'])
            except Exception as e:
                log.warning('V43 momentum daily %s failed: %s',rec['symbol'],e)
            # Keep chart requests comfortably under the broker throttle and
            # leave headroom for minute backfill / metrics traffic.
            _time.sleep(0.55)

        # Build matches from whatever current candidates already have fresh cache.
        matches=[]
        now=_time.time()
        for rec in candidates:
            key=(str(rec['symbol']).upper(),str(rec['exchange']).upper())
            feat=(self._momentum_daily_cache or {}).get(key) or {}
            if not feat:
                continue
            # Ignore stale entries from symbols no longer recently evaluated.
            if now-float(feat.get('_cached_at',0) or 0)>1800:
                continue
            if not feat.get('momentum_match'):
                continue
            x=rec['raw']
            matches.append({
                'symbol':rec['symbol'],'exchange':rec['exchange'],
                'name':x.get('stk_enm') or x.get('stk_nm') or '',
                'price':abs(num(x.get('cur_prc'))),'change_pct':num(x.get('flu_rt')),
                'volume':abs(num(x.get('acc_trde_qty') or x.get('trde_qty'))),
                'dollar_volume':abs(num(x.get('trde_prica'))),
                'volume_rank':rec['volume_rank'],'dollar_rank':rec['dollar_rank'],
                'momentum_match':True,'macd_cross_5':True,
                'macd_zero_cross_bars_ago':feat.get('macd_zero_cross_bars_ago'),
                'high_52w':feat.get('high_52w'),'high_52w_gap_pct':feat.get('high_52w_gap_pct'),
                'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)',
                'momentum_eval_mode':'ROTATING_BATCH_V43'
            })

        matches.sort(key=lambda r:(min(r['volume_rank'],r['dollar_rank']),-float(r.get('high_52w_gap_pct') or -999)))
        return matches
'''


def main():
    s=KIWOOM.read_text()
    pat=r"    def _v39_momentum_rank_candidates\(self, volume_rows, dollar_rows\):\n.*?(?=\n    def |\n    async def |\nclass |\Z)"
    m=re.search(pat,s,re.S)
    if not m:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: _v39_momentum_rank_candidates')
    s=s[:m.start()]+NEW_FUNC+s[m.end():]
    KIWOOM.write_text(s)
    print('FINDER_MOMENTUM_ROTATION_V43_OK')

if __name__=='__main__':
    main()
