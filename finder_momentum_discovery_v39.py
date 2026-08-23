from pathlib import Path

KIWOOM=Path('live_server/kiwoom.py')
KOREA=Path('live_server/korea.py')

USA_HELPER=r'''
    # ===== V39 MOMENTUM DISCOVERY: DAILY MACD + 52W HIGH =====
    @staticmethod
    def _v39_ema(values, span):
        if not values:
            return []
        alpha=2.0/(float(span)+1.0)
        out=[float(values[0])]
        for v in values[1:]:
            out.append(alpha*float(v)+(1.0-alpha)*out[-1])
        return out

    def _v39_daily_momentum_feature(self, symbol, exchange):
        import time as _time
        key=(str(symbol).upper(),str(exchange).upper())
        now=_time.time()
        cached=(self._momentum_daily_cache or {}).get(key)
        if cached and now-float(cached.get('_cached_at',0))<900:
            return dict(cached)

        start=(datetime.now(timezone.utc)-timedelta(days=430)).strftime('%Y%m%d')
        rows=[]; next_key=''; pages=0
        while pages<5:
            hdr=self.headers('usa06012')
            if next_key:
                hdr['cont-yn']='Y'; hdr['next-key']=next_key
            r=requests.post(self.s.rest_base+'/api/us/chart',headers=hdr,json={
                'stex_tp':exchange,'stk_cd':symbol,'strt_dt':start,
                'upd_stkpc_tp':'1','exrt_appl_tp':'0'
            },timeout=25)
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"usa06012 {symbol}/{exchange}: {d.get('return_code')} {d.get('return_msg')}")
            raw=d.get('result_list') or []
            rows.extend(x for x in raw if isinstance(x,dict))
            pages+=1
            cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
            next_key=str(r.headers.get('next-key') or r.headers.get('Next-Key') or '')
            if cont!='Y' or not next_key:
                break

        cleaned=[]
        for x in rows:
            dt=str(x.get('dt') or x.get('date') or '').replace('-','').strip()
            close=abs(num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close')))
            high=abs(num(x.get('high_pric') if x.get('high_pric') is not None else x.get('high')))
            if len(dt)>=8 and close>0:
                cleaned.append((dt[:8],close,high or close))
        uniq={dt:(close,high) for dt,close,high in cleaned}
        seq=[(dt,*uniq[dt]) for dt in sorted(uniq)]
        if len(seq)<35:
            out={'ok':False,'symbol':symbol,'reason':f'insufficient_daily_rows:{len(seq)}','_cached_at':now}
            self._momentum_daily_cache[key]=out
            return dict(out)

        closes=[x[1] for x in seq]
        highs=[x[2] for x in seq]
        ema12=self._v39_ema(closes,12); ema26=self._v39_ema(closes,26)
        macd=[a-b for a,b in zip(ema12,ema26)]
        cross_offsets=[]
        for i in range(1,len(macd)):
            if macd[i-1] <= 0 < macd[i]:
                cross_offsets.append(len(macd)-1-i)
        recent_cross=min(cross_offsets) if cross_offsets else None
        macd_cross_5=bool(recent_cross is not None and recent_cross<=5)
        window=min(252,len(highs))
        high52=max(highs[-window:]) if window else 0.0
        current=closes[-1]
        gap52=((current/high52)-1.0)*100.0 if high52 else None
        near_52w=bool(gap52 is not None and gap52>=-10.0)
        out={
            'ok':True,'symbol':symbol,'daily_rows':len(seq),'pages':pages,
            'current_close':round(current,6),'high_52w':round(high52,6),
            'high_52w_gap_pct':round(gap52,3) if gap52 is not None else None,
            'macd':round(macd[-1],6),'macd_zero_cross_bars_ago':recent_cross,
            'macd_cross_5':macd_cross_5,'near_52w_high':near_52w,
            'momentum_match':bool(macd_cross_5 and near_52w),
            '_cached_at':now,
        }
        self._momentum_daily_cache[key]=out
        return dict(out)

    def _v39_momentum_rank_candidates(self, volume_rows, dollar_rows):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        pool={}
        def add(rows, label):
            for rank,x in enumerate((rows or [])[:100],1):
                sym=str(x.get('stk_cd') or '').upper().strip()
                if not sym:
                    continue
                rec=pool.setdefault(sym,{'symbol':sym,'raw':x,'volume_rank':9999,'dollar_rank':9999})
                rec[label+'_rank']=min(rec[label+'_rank'],rank)
                if not rec.get('raw'):
                    rec['raw']=x
        add(volume_rows,'volume'); add(dollar_rows,'dollar')

        exmap={'1':'NY','2':'ND','3':'NA','NYSE':'NY','NASDAQ':'ND','AMEX':'NA','NY':'NY','ND':'ND','NA':'NA','AM':'NA'}
        candidates=[]
        for rec in pool.values():
            x=rec['raw']; price=abs(num(x.get('cur_prc')))
            if price < float(self.s.discovery_min_price):
                continue
            rawex=str(x.get('stex_tp') or '').upper().strip()
            ex=exmap.get(rawex) or self.active_exchange(rec['symbol'])
            rec['exchange']=ex
            candidates.append(rec)

        matches=[]
        with ThreadPoolExecutor(max_workers=4) as exe:
            futs={exe.submit(self._v39_daily_momentum_feature,r['symbol'],r['exchange']):r for r in candidates}
            for fut in as_completed(futs):
                rec=futs[fut]
                try:
                    feat=fut.result()
                except Exception as e:
                    log.warning('V39 momentum daily %s failed: %s',rec['symbol'],e)
                    continue
                if feat.get('momentum_match'):
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
                        'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'
                    })
        matches.sort(key=lambda r:(min(r['volume_rank'],r['dollar_rank']),-float(r.get('high_52w_gap_pct') or -999)))
        return matches
'''

KR_HELPER=r'''
    # ===== V39 MOMENTUM DISCOVERY: DAILY MACD + 52W HIGH =====
    @staticmethod
    def _v39_ema(values, span):
        if not values:
            return []
        alpha=2.0/(float(span)+1.0)
        out=[float(values[0])]
        for v in values[1:]:
            out.append(alpha*float(v)+(1.0-alpha)*out[-1])
        return out

    def _v39_daily_momentum_feature(self, stk_cd):
        import time as _time
        code=_clean_code(stk_cd)
        now=_time.time()
        cached=(self._momentum_daily_cache or {}).get(code)
        if cached and now-float(cached.get('_cached_at',0))<900:
            return dict(cached)
        rows=[]; next_key=''; pages=0
        while pages<3:
            hdr=self.k.headers('ka10081')
            if next_key:
                hdr['cont-yn']='Y'; hdr['next-key']=next_key
            r=requests.post(self.k.s.rest_base+'/api/dostk/chart',headers=hdr,json={
                'stk_cd':code,'base_dt':datetime.now(timezone.utc).astimezone().strftime('%Y%m%d'),'upd_stkpc_tp':'1'
            },timeout=25)
            d=r.json()
            if d.get('return_code') not in (None,0):
                raise RuntimeError(f"ka10081 {code}: {d.get('return_code')} {d.get('return_msg')}")
            raw=d.get('stk_dt_pole_chart_qry') or d.get('stk_dt_chart_qry') or []
            if not raw:
                for v in d.values():
                    if isinstance(v,list): raw=v; break
            rows.extend(x for x in raw if isinstance(x,dict))
            pages+=1
            cont=str(r.headers.get('cont-yn') or r.headers.get('Cont-Yn') or '').upper()
            next_key=str(r.headers.get('next-key') or r.headers.get('Next-Key') or '')
            if cont!='Y' or not next_key:
                break

        cleaned=[]
        for x in rows:
            dt=str(x.get('dt') or x.get('stk_dt') or x.get('base_dt') or '').replace('-','').strip()
            close=abs(_num(x.get('cur_prc') if x.get('cur_prc') is not None else x.get('close')))
            high=abs(_num(x.get('high_pric') if x.get('high_pric') is not None else x.get('high')))
            if len(dt)>=8 and close>0:
                cleaned.append((dt[:8],close,high or close))
        uniq={dt:(close,high) for dt,close,high in cleaned}
        seq=[(dt,*uniq[dt]) for dt in sorted(uniq)]
        if len(seq)<35:
            out={'ok':False,'symbol':code,'reason':f'insufficient_daily_rows:{len(seq)}','_cached_at':now}
            self._momentum_daily_cache[code]=out
            return dict(out)
        closes=[x[1] for x in seq]; highs=[x[2] for x in seq]
        ema12=self._v39_ema(closes,12); ema26=self._v39_ema(closes,26)
        macd=[a-b for a,b in zip(ema12,ema26)]
        offsets=[len(macd)-1-i for i in range(1,len(macd)) if macd[i-1] <= 0 < macd[i]]
        recent=min(offsets) if offsets else None
        macd_cross_5=bool(recent is not None and recent<=5)
        high52=max(highs[-min(252,len(highs)):])
        current=closes[-1]; gap52=((current/high52)-1.0)*100.0 if high52 else None
        near_52w=bool(gap52 is not None and gap52>=-10.0)
        out={'ok':True,'symbol':code,'daily_rows':len(seq),'pages':pages,
             'current_close':current,'high_52w':high52,'high_52w_gap_pct':round(gap52,3) if gap52 is not None else None,
             'macd':macd[-1],'macd_zero_cross_bars_ago':recent,'macd_cross_5':macd_cross_5,
             'near_52w_high':near_52w,'momentum_match':bool(macd_cross_5 and near_52w),'_cached_at':now}
        self._momentum_daily_cache[code]=out
        return dict(out)
'''


def patch_usa():
    s=KIWOOM.read_text()
    if 'self._momentum_daily_cache={}' not in s:
        anchor="        self.discovery = {'symbols': list(settings.symbols), 'rows': [], 'updated_at': None, 'count': len(settings.symbols), 'core': list(settings.core_symbols), 'exchanges': {}}\n"
        if anchor not in s: raise SystemExit('USA_INIT_ANCHOR_NOT_FOUND')
        s=s.replace(anchor,anchor+'        self._momentum_daily_cache={}\n',1)
    if 'V39 MOMENTUM DISCOVERY: DAILY MACD + 52W HIGH' not in s:
        anchor='    def discover_universe(self) -> dict:\n'
        if anchor not in s: raise SystemExit('USA_DISCOVER_ANCHOR_NOT_FOUND')
        s=s.replace(anchor,USA_HELPER+'\n'+anchor,1)

    old="""        result=merge_rankings(\n            volume,dollar,core,self.s.discovery_limit,\n            self.s.discovery_min_price,self.s.discovery_min_dollar,\n            gainers=gainers,losers=losers,volume_surge=surge\n        )\n        exchanges={r['symbol']:r.get('exchange') for r in result.rows if r.get('exchange')}\n"""
    new="""        result=merge_rankings(\n            volume,dollar,core,self.s.discovery_limit,\n            self.s.discovery_min_price,self.s.discovery_min_dollar,\n            gainers=gainers,losers=losers,volume_surge=surge\n        )\n\n        # V39: independent momentum-discovery lane using the published search formula.\n        momentum=self._v39_momentum_rank_candidates(volume,dollar)\n        existing={r.get('symbol'):r for r in result.rows}\n        for m in momentum:\n            sym=m.get('symbol')\n            if sym in existing:\n                existing[sym].update({k:v for k,v in m.items() if k.startswith('momentum_') or k.startswith('macd_') or k.startswith('high_52w')})\n                existing[sym]['origin']='MOMENTUM' if existing[sym].get('origin')!='CORE' else 'CORE'\n                existing[sym]['discovery_score']=round(float(existing[sym].get('discovery_score') or 0)+30.0,1)\n            else:\n                row=dict(m)\n                row.update({'gainer_rank':9999,'loser_rank':9999,'surge_rank':9999,'surge_pct':0.0,\n                            'sources':'momentum','chase_risk':'NORMAL','discovery_score':130.0,\n                            'asset_type':'STOCK','quality_grade':'B_EVENT','quality_reasons':'MOMENTUM_DISCOVERY',\n                            'quality_gate':'MOMENTUM_V39','origin':'MOMENTUM'})\n                result.rows.append(row); existing[sym]=row\n        result.rows.sort(key=lambda r:(1 if r.get('momentum_match') else 0,float(r.get('discovery_score') or 0)),reverse=True)\n        result.symbols=list(dict.fromkeys([r.get('symbol') for r in result.rows if r.get('symbol')]))\n\n        exchanges={r['symbol']:r.get('exchange') for r in result.rows if r.get('exchange')}\n"""
    if old in s:
        s=s.replace(old,new,1)
    elif 'momentum=self._v39_momentum_rank_candidates(volume,dollar)' not in s:
        raise SystemExit('USA_MERGE_TARGET_NOT_FOUND')

    old2="""            'quality_gate':'QUALITY_GATE_USA_V1'\n        }\n"""
    new2="""            'quality_gate':'QUALITY_GATE_USA_V1',\n            'momentum_count':len(momentum),\n            'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'\n        }\n"""
    if old2 in s:
        s=s.replace(old2,new2,1)
    KIWOOM.write_text(s)


def patch_korea():
    s=KOREA.read_text()
    if 'self._momentum_daily_cache={}' not in s:
        anchor='        self.cap_rank_enabled=False\n'
        if anchor not in s: raise SystemExit('KR_INIT_ANCHOR_NOT_FOUND')
        s=s.replace(anchor,anchor+'        self._momentum_daily_cache={}\n',1)
    if s.count('V39 MOMENTUM DISCOVERY: DAILY MACD + 52W HIGH')==0:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s: raise SystemExit('KR_DISCOVER_ANCHOR_NOT_FOUND')
        s=s.replace(anchor,KR_HELPER+'\n'+anchor,1)

    old="""        meta={}; cap_rank_enabled=False; meta_error=None\n"""
    new="""        # V39: enrich only the published liquidity lane (Top100 value OR volume).\n        from concurrent.futures import ThreadPoolExecutor, as_completed\n        momentum_targets=[r for r in rows if r.get('value_rank',9999)<=100 or r.get('volume_rank',9999)<=100]\n        with ThreadPoolExecutor(max_workers=4) as exe:\n            futs={exe.submit(self._v39_daily_momentum_feature,r['symbol']):r for r in momentum_targets}\n            for fut in as_completed(futs):\n                row=futs[fut]\n                try: feat=fut.result()\n                except Exception: continue\n                row.update({k:v for k,v in feat.items() if k in ('momentum_match','macd_cross_5','macd_zero_cross_bars_ago','high_52w','high_52w_gap_pct','near_52w_high')})\n                if feat.get('momentum_match'):\n                    row['score']=round(min(130.0,float(row.get('score') or 0)+30.0),1)\n                    row['origin']='MOMENTUM'\n                    row['momentum_formula']='MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'\n\n        meta={}; cap_rank_enabled=False; meta_error=None\n"""
    if old in s:
        s=s.replace(old,new,1)
    elif 'momentum_targets=[r for r in rows' not in s:
        raise SystemExit('KR_META_TARGET_NOT_FOUND')

    oldsort="""        passed=sorted(passed,key=lambda x:(x['score'],x['source_count'],x['trading_value']),reverse=True)[:max(10,int(limit))]\n"""
    newsort="""        passed=sorted(passed,key=lambda x:(1 if x.get('momentum_match') else 0,x['score'],x['source_count'],x['trading_value']),reverse=True)[:max(10,int(limit))]\n"""
    if oldsort in s: s=s.replace(oldsort,newsort,1)

    old3="""            'metadata_error':meta_error\n        }\n"""
    new3="""            'metadata_error':meta_error,\n            'momentum_count':len([r for r in passed if r.get('momentum_match')]),\n            'momentum_formula':'MACD0_CROSS_5 AND 52W_HIGH_WITHIN_10 AND (VOL_TOP100 OR VALUE_TOP100)'\n        }\n"""
    if old3 in s: s=s.replace(old3,new3,1)
    KOREA.write_text(s)


def main():
    patch_usa(); patch_korea()
    print('FINDER_MOMENTUM_DISCOVERY_V39_OK')

if __name__=='__main__':
    main()
