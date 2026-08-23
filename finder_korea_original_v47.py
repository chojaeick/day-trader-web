from pathlib import Path
import re

API=Path('live_server/api.py')
KOREA=Path('live_server/korea.py')

KOREA_PATCH=r'''

    # ===== V47 ORIGINAL KIWOOOM MOMENTUM FINDER (KOREA) =====
    @staticmethod
    def _v47_ema(values, span):
        if not values:
            return []
        alpha=2.0/(float(span)+1.0)
        out=[float(values[0])]
        for v in values[1:]:
            out.append(alpha*float(v)+(1.0-alpha)*out[-1])
        return out

    def _v47_daily_original_feature(self, stk_cd):
        import time as _time
        code=_clean_code(stk_cd)
        now=_time.time()
        if not hasattr(self,'_v47_original_cache'):
            self._v47_original_cache={}
        cached=self._v47_original_cache.get(code)
        if cached and now-float(cached.get('_cached_at',0) or 0)<1800:
            return dict(cached)

        rows=[]; next_key=''; pages=0
        while pages<5:
            hdr=self.k.headers('ka10081')
            if next_key:
                hdr['cont-yn']='Y'; hdr['next-key']=next_key
            r=requests.post(self.k.s.rest_base+'/api/dostk/chart',headers=hdr,json={
                'stk_cd':code,
                'base_dt':datetime.now(timezone.utc).astimezone().strftime('%Y%m%d'),
                'upd_stkpc_tp':'1'
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
            _time.sleep(0.18)

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
            self._v47_original_cache[code]=out
            return dict(out)

        closes=[x[1] for x in seq]; highs=[x[2] for x in seq]
        ema12=self._v47_ema(closes,12); ema26=self._v47_ema(closes,26)
        macd=[a-b for a,b in zip(ema12,ema26)]
        offsets=[len(macd)-1-i for i in range(1,len(macd)) if macd[i-1] <= 0 < macd[i]]
        recent=min(offsets) if offsets else None
        macd_cross_5=bool(recent is not None and recent<=5)
        window=min(252,len(highs))
        high52=max(highs[-window:]) if window else 0.0
        current=closes[-1]
        gap52=((current/high52)-1.0)*100.0 if high52 else None
        near52=bool(gap52 is not None and gap52>=-10.0)
        out={
            'ok':True,'symbol':code,'daily_rows':len(seq),'pages':pages,
            'current_close':current,'high_52w':high52,
            'high_52w_gap_pct':round(gap52,3) if gap52 is not None else None,
            'macd':macd[-1],'macd_zero_cross_bars_ago':recent,
            'macd_cross_5':macd_cross_5,'near_52w_high':near52,
            'momentum_match':bool(macd_cross_5 and near52),
            '_cached_at':now,
        }
        self._v47_original_cache[code]=out
        return dict(out)

    def original_momentum_scan_v47(self, batch_size=20):
        import time as _time
        merged={}
        # Pull KOSPI/KOSDAQ value and volume ranks only, then construct a global Top100 lane.
        for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
            for label,rows in [('value',self._trading_value(mrkt_tp)),('volume',self._today_volume(mrkt_tp))]:
                for x in rows or []:
                    sym=_clean_code(x.get('stk_cd'))
                    if not sym: continue
                    r=merged.setdefault(sym,{'symbol':sym,'name':str(x.get('stk_nm') or '').strip(),'market':market,
                                             'trading_value':0.0,'volume':0.0,'raw':x})
                    if not r.get('name'): r['name']=str(x.get('stk_nm') or '').strip()
                    r['trading_value']=max(r['trading_value'],abs(_num(x.get('trde_prica') or x.get('trde_amt') or x.get('acc_trde_prica'))))
                    r['volume']=max(r['volume'],abs(_num(x.get('now_trde_qty') or x.get('trde_qty') or x.get('acc_trde_qty'))))

        allrows=list(merged.values())
        by_value=sorted(allrows,key=lambda r:r['trading_value'],reverse=True)
        by_volume=sorted(allrows,key=lambda r:r['volume'],reverse=True)
        value_rank={r['symbol']:i+1 for i,r in enumerate(by_value[:100])}
        volume_rank={r['symbol']:i+1 for i,r in enumerate(by_volume[:100])}
        symbols=set(value_rank)|set(volume_rank)

        # Use security master to exclude ETF/ETN/SPAC/preferred where detectable.
        meta={}
        try:
            meta,_=self._load_stock_metadata(False)
        except Exception:
            meta={}
        candidates=[]; excluded=[]
        for r in allrows:
            sym=r['symbol']
            if sym not in symbols: continue
            r['value_rank']=value_rank.get(sym,9999); r['volume_rank']=volume_rank.get(sym,9999)
            m=meta.get(sym) or {}
            text=' '.join(str(m.get(k) or '') for k in ('name','stk_nm','stock_name','type','kind','market')).upper()
            nm=(r.get('name') or '').upper()
            reason=None
            if 'ETF' in text or 'ETN' in text or 'ETF' in nm or 'ETN' in nm:
                reason='ETF_ETN'
            elif '스팩' in r.get('name','') or 'SPAC' in text or 'SPAC' in nm:
                reason='SPAC'
            elif r.get('name','').endswith('우') or '우선주' in r.get('name',''):
                reason='PREFERRED'
            if reason:
                excluded.append({**r,'exclude_reason':reason}); continue
            candidates.append(r)
        candidates.sort(key=lambda r:(min(r['value_rank'],r['volume_rank']),r['symbol']))

        if not hasattr(self,'_v47_original_cursor'): self._v47_original_cursor=0
        if not candidates:
            return {'ok':True,'candidate_count':0,'rows':[],'excluded_count':len(excluded)}
        bs=max(1,min(int(batch_size),40,len(candidates)))
        start=int(self._v47_original_cursor)%len(candidates)
        batch=[candidates[(start+i)%len(candidates)] for i in range(bs)]
        self._v47_original_cursor=(start+bs)%len(candidates)
        for r in batch:
            try:
                self._v47_daily_original_feature(r['symbol'])
            except Exception as e:
                if not hasattr(self,'_v47_original_cache'): self._v47_original_cache={}
                self._v47_original_cache[r['symbol']]={'ok':False,'symbol':r['symbol'],'reason':str(e)[:180],'_cached_at':_time.time()}
            _time.sleep(0.35)

        now=_time.time(); enriched=[]
        for r in candidates:
            feat=(getattr(self,'_v47_original_cache',{}) or {}).get(r['symbol']) or {}
            if not feat or now-float(feat.get('_cached_at',0) or 0)>1800: continue
            row={**r,**{k:v for k,v in feat.items() if not k.startswith('_')}}
            enriched.append(row)
        ok=[r for r in enriched if r.get('ok')]
        matches=[r for r in ok if r.get('momentum_match')]
        matches.sort(key=lambda r:(min(r.get('value_rank',9999),r.get('volume_rank',9999)),r['symbol']))
        return {
            'ok':True,
            'formula':'MACD_ZERO_CROSS_0_TO_5 AND HIGH52_GAP_GE_-10 AND (VALUE_TOP100 OR VOLUME_TOP100)',
            'cursor':self._v47_original_cursor,
            'candidate_count':len(candidates),'excluded_count':len(excluded),
            'evaluated_count':len(enriched),'feature_ok_count':len(ok),'feature_fail_count':len(enriched)-len(ok),
            'macd_cross_5_count':sum(1 for r in ok if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in ok if r.get('near_52w_high')),
            'match_count':len(matches),
            'rows':matches,
            'evaluated_rows':enriched,
            'excluded_rows':excluded[:50],
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-original')
async def v47_korea_momentum_original(batch_size:int=20):
    return await asyncio.to_thread(korea.original_momentum_scan_v47,batch_size)
'''

def main():
    s=KOREA.read_text()
    if 'def original_momentum_scan_v47' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1)
        KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/korea-momentum-original' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: app=FastAPI')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1)
        API.write_text(a)
    print('FINDER_KOREA_ORIGINAL_V47_OK')

if __name__=='__main__':
    main()
