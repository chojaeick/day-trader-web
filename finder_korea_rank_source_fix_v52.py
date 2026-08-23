from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== V52 KOREA FINDER: INDEPENDENT KIWOOOM VALUE/VOLUME RANK SOURCES =====
    def momentum_finder_v52(self, batch_size=20, limit=40):
        import time as _time

        # Important: never infer ranks by sorting merged rows.  ka10032 and ka10030
        # already return independent ranking lists; preserve each list's order.
        value_rows=[]
        volume_rows=[]
        for mrkt_tp, market in [('001','KOSPI'),('101','KOSDAQ')]:
            for x in (self._trading_value(mrkt_tp) or []):
                if isinstance(x,dict): value_rows.append((market,x))
            for x in (self._today_volume(mrkt_tp) or []):
                if isinstance(x,dict): volume_rows.append((market,x))

        def build_rank(rows, lane):
            out={}; raw_by={}; name_by={}; market_by={}
            # Kiwoom's now_rank is the authoritative rank when present.  Otherwise
            # use response order within the returned lane.
            fallback=0
            for market,x in rows:
                sym=_clean_code(x.get('stk_cd'))
                if not sym: continue
                fallback+=1
                try:
                    rank=int(float(str(x.get('now_rank') or '').replace(',','').strip()))
                except Exception:
                    rank=fallback
                if rank < 1 or rank > 100: continue
                if sym not in out or rank < out[sym]:
                    out[sym]=rank; raw_by[sym]=x; market_by[sym]=market
                    name_by[sym]=str(x.get('stk_nm') or '').strip()
            return out,raw_by,name_by,market_by

        vrank,vraw,vname,vmarket=build_rank(value_rows,'value')
        qrank,qraw,qname,qmarket=build_rank(volume_rows,'volume')
        symbols=set(vrank)|set(qrank)

        candidates=[]
        for sym in symbols:
            raw=vraw.get(sym) or qraw.get(sym) or {}
            name=vname.get(sym) or qname.get(sym) or str(raw.get('stk_nm') or '').strip()
            candidates.append({
                'symbol':sym,'name':name,'market':vmarket.get(sym) or qmarket.get(sym),
                'value_rank':vrank.get(sym,9999),'volume_rank':qrank.get(sym,9999),
                'rank_sources':(["VALUE_KA10032"] if sym in vrank else [])+(["VOLUME_KA10030"] if sym in qrank else []),
                'raw_value':vraw.get(sym),'raw_volume':qraw.get(sym),
            })
        candidates.sort(key=lambda r:(min(r['value_rank'],r['volume_rank']),r['symbol']))

        if not hasattr(self,'_v52_cursor'): self._v52_cursor=0
        bs=max(1,min(int(batch_size),40,len(candidates))) if candidates else 0
        if candidates and bs:
            start=int(self._v52_cursor)%len(candidates)
            batch=[candidates[(start+i)%len(candidates)] for i in range(bs)]
            self._v52_cursor=(start+bs)%len(candidates)
            for r in batch:
                try: self._v47_daily_original_feature(r['symbol'])
                except Exception: pass
                _time.sleep(0.30)

        # Operational exclusions: keep ETFs, exclude preferred/SPAC/ETN by name.
        etf_prefix=('KODEX ','TIGER ','RISE ','PLUS ','ACE ','SOL ','HANARO ','KOSEF ','TIMEFOLIO ','KBSTAR ','ARIRANG ','FOCUS ','WOORI ','1Q ','HK ')
        rows=[]; excluded=[]; now=_time.time()
        cache=getattr(self,'_v47_original_cache',{}) or {}
        for r in candidates:
            feat=cache.get(r['symbol']) or {}
            if not feat or now-float(feat.get('_cached_at',0) or 0)>1800: continue
            row={**r,**{k:v for k,v in feat.items() if not k.startswith('_')}}
            nm=(row.get('name') or '').strip(); up=nm.upper()
            reason=None
            if '스팩' in nm or 'SPAC' in up: reason='SPAC'
            elif ' ETN' in (' '+up) or up.endswith('ETN'): reason='ETN'
            elif nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or __import__('re').search(r'\d+우B$',nm): reason='PREFERRED'
            if reason:
                row['exclude_reason']=reason; excluded.append(row); continue
            row['instrument_type']='ETF' if (up.startswith(etf_prefix) or ' ETF' in (' '+up)) else 'STOCK'
            if row.get('macd_cross_5') and row.get('near_52w_high'):
                row['finder_tag']='PRIMARY_SIGNAL'; row['finder_score']=130.0
            elif row.get('macd_cross_5'):
                row['finder_tag']='MACD_FRESH'; row['finder_score']=110.0-min(row['value_rank'],row['volume_rank'])*0.7
            elif row.get('near_52w_high'):
                row['finder_tag']='LEADER_52W'; row['finder_score']=105.0-min(row['value_rank'],row['volume_rank'])*0.7
            else:
                row['finder_tag']='LIQUID_WATCH'; row['finder_score']=100.0-min(row['value_rank'],row['volume_rank'])*0.7
            rows.append(row)
        rows.sort(key=lambda r:(r.get('finder_score',0),-min(r['value_rank'],r['volume_rank'])),reverse=True)
        rows=rows[:max(1,min(int(limit),100))]
        return {
            'ok':True,'finder_mode':'BROAD_LIQUIDITY_ETF_INCLUDED_V52_INDEPENDENT_RANKS',
            'value_top100_count':len(vrank),'volume_top100_count':len(qrank),'candidate_count':len(candidates),
            'evaluated_count':len(rows)+len(excluded),'finder_count':len(rows),'excluded_output_count':len(excluded),
            'stock_count':sum(1 for r in rows if r.get('instrument_type')=='STOCK'),
            'etf_count':sum(1 for r in rows if r.get('instrument_type')=='ETF'),
            'primary_signal_count':sum(1 for r in rows if r.get('finder_tag')=='PRIMARY_SIGNAL'),
            'macd_fresh_count':sum(1 for r in rows if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in rows if r.get('near_52w_high')),
            'cursor':getattr(self,'_v52_cursor',0),'rows':rows,'excluded_rows':excluded[:30],
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-finder-v52')
async def v52_korea_momentum_finder(batch_size:int=20,limit:int=40):
    return await asyncio.to_thread(korea.momentum_finder_v52,batch_size,limit)
'''

def main():
    s=KOREA.read_text()
    if 'def momentum_finder_v52' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1); KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/korea-momentum-finder-v52' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1); API.write_text(a)
    print('FINDER_KOREA_RANK_SOURCE_FIX_V52_OK')

if __name__=='__main__': main()
