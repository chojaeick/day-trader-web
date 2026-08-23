from pathlib import Path

KIWOOM=Path('live_server/kiwoom.py')
API=Path('live_server/api.py')

KI_PATCH=r'''
    # ===== V40 MOMENTUM DISCOVERY DIAGNOSTIC =====
    def v40_momentum_diagnostic(self, volume_rows, dollar_rows):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        pool={}
        def add(rows,label):
            for rank,x in enumerate((rows or [])[:100],1):
                sym=str(x.get('stk_cd') or '').upper().strip()
                if not sym: continue
                rec=pool.setdefault(sym,{'symbol':sym,'raw':x,'volume_rank':9999,'dollar_rank':9999})
                rec[label+'_rank']=min(rec[label+'_rank'],rank)
        add(volume_rows,'volume'); add(dollar_rows,'dollar')
        exmap={'1':'NY','2':'ND','3':'NA','NYSE':'NY','NASDAQ':'ND','AMEX':'NA','NY':'NY','ND':'ND','NA':'NA','AM':'NA'}
        candidates=[]
        for rec in pool.values():
            x=rec['raw']; price=abs(num(x.get('cur_prc')))
            if price < float(self.s.discovery_min_price): continue
            rawex=str(x.get('stex_tp') or '').upper().strip()
            rec['exchange']=exmap.get(rawex) or self.active_exchange(rec['symbol'])
            candidates.append(rec)

        detail=[]
        with ThreadPoolExecutor(max_workers=4) as exe:
            futs={exe.submit(self._v39_daily_momentum_feature,r['symbol'],r['exchange']):r for r in candidates}
            for fut in as_completed(futs):
                rec=futs[fut]
                row={'symbol':rec['symbol'],'exchange':rec['exchange'],'volume_rank':rec['volume_rank'],'dollar_rank':rec['dollar_rank']}
                try:
                    feat=fut.result() or {}
                    row.update({k:feat.get(k) for k in ('ok','reason','daily_rows','pages','current_close','high_52w','high_52w_gap_pct','macd','macd_zero_cross_bars_ago','macd_cross_5','near_52w_high','momentum_match')})
                except Exception as e:
                    row.update({'ok':False,'reason':'exception:'+str(e)})
                detail.append(row)
        detail.sort(key=lambda r:min(r.get('volume_rank',9999),r.get('dollar_rank',9999)))
        ok=[r for r in detail if r.get('ok')]
        return {
            'candidate_count':len(candidates),
            'feature_ok_count':len(ok),
            'feature_fail_count':len(detail)-len(ok),
            'macd_cross_5_count':sum(1 for r in ok if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in ok if r.get('near_52w_high')),
            'momentum_match_count':sum(1 for r in ok if r.get('momentum_match')),
            'formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)',
            'rows':detail,
        }
'''

API_PATCH=r'''

@app.get('/api/v5/momentum-diagnostic/USA')
def v40_momentum_diagnostic_usa():
    try:
        volume=k.volume_rank()
        dollar=k.dollar_rank()
        return {'ok':True,**k.v40_momentum_diagnostic(volume,dollar)}
    except Exception as e:
        return {'ok':False,'error':str(e)}
'''

def main():
    s=KIWOOM.read_text()
    if 'V40 MOMENTUM DISCOVERY DIAGNOSTIC' not in s:
        anchor='    def _v39_momentum_rank_candidates(self, volume_rows, dollar_rows):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: kiwoom v39 anchor')
        s=s.replace(anchor,KI_PATCH+'\n'+anchor,1)
        KIWOOM.write_text(s)
    a=API.read_text()
    if '/api/v5/momentum-diagnostic/USA' not in a:
        anchor="manual_scan_state={'last_started_monotonic':0.0,'last_result':None}\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: api anchor')
        a=a.replace(anchor,anchor+API_PATCH,1)
        API.write_text(a)
    print('FINDER_MOMENTUM_DIAG_V40_OK')

if __name__=='__main__':
    main()
