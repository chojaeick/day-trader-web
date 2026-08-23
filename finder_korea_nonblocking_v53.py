from pathlib import Path

KOREA=Path('live_server/korea.py')
API=Path('live_server/api.py')

KOREA_PATCH=r'''

    # ===== V53 NONBLOCKING KOREA FINDER =====
    def momentum_rank_snapshot_v53(self):
        import time as _time
        t0=_time.time()
        value_rows=[]; volume_rows=[]
        for mrkt_tp,market in [('001','KOSPI'),('101','KOSDAQ')]:
            for x in (self._trading_value(mrkt_tp) or []):
                if isinstance(x,dict): value_rows.append((market,x))
            for x in (self._today_volume(mrkt_tp) or []):
                if isinstance(x,dict): volume_rows.append((market,x))

        def lane(rows):
            ranks={}; raws={}; names={}; markets={}; fallback=0
            for market,x in rows:
                sym=_clean_code(x.get('stk_cd'))
                if not sym: continue
                fallback+=1
                try: rank=int(float(str(x.get('now_rank') or '').replace(',','').strip()))
                except Exception: rank=fallback
                if rank<1 or rank>100: continue
                if sym not in ranks or rank<ranks[sym]:
                    ranks[sym]=rank; raws[sym]=x; names[sym]=str(x.get('stk_nm') or '').strip(); markets[sym]=market
            return ranks,raws,names,markets

        vr,vraw,vname,vmkt=lane(value_rows)
        qr,qraw,qname,qmkt=lane(volume_rows)
        syms=set(vr)|set(qr)
        rows=[]
        for sym in syms:
            rows.append({
                'symbol':sym,'name':vname.get(sym) or qname.get(sym) or '',
                'market':vmkt.get(sym) or qmkt.get(sym),
                'value_rank':vr.get(sym,9999),'volume_rank':qr.get(sym,9999),
                'rank_sources':(["VALUE_KA10032"] if sym in vr else [])+(["VOLUME_KA10030"] if sym in qr else []),
            })
        rows.sort(key=lambda r:(min(r['value_rank'],r['volume_rank']),r['symbol']))
        self._v53_rank_rows=rows
        self._v53_rank_cached_at=_time.time()
        return {'ok':True,'value_top100_count':len(vr),'volume_top100_count':len(qr),'union_count':len(rows),'rows':rows,'elapsed_sec':round(_time.time()-t0,3)}

    def momentum_finder_v53(self, batch_size=6, limit=40):
        import time as _time, re as _re
        # Refresh only the cheap rank snapshot when absent/stale (60s).
        now=_time.time()
        if not hasattr(self,'_v53_rank_rows') or now-float(getattr(self,'_v53_rank_cached_at',0) or 0)>60:
            self.momentum_rank_snapshot_v53()
        candidates=list(getattr(self,'_v53_rank_rows',[]) or [])
        if not hasattr(self,'_v53_cursor'): self._v53_cursor=0
        bs=max(1,min(int(batch_size),8,len(candidates))) if candidates else 0
        if candidates and bs:
            start=int(self._v53_cursor)%len(candidates)
            batch=[candidates[(start+i)%len(candidates)] for i in range(bs)]
            self._v53_cursor=(start+bs)%len(candidates)
            for r in batch:
                try: self._v47_daily_original_feature(r['symbol'])
                except Exception: pass
                _time.sleep(0.15)

        cache=getattr(self,'_v47_original_cache',{}) or {}
        rows=[]; excluded=[]
        etf_prefix=('KODEX ','TIGER ','RISE ','PLUS ','ACE ','SOL ','HANARO ','KOSEF ','TIMEFOLIO ','KBSTAR ','ARIRANG ','FOCUS ','WOORI ','1Q ','HK ')
        for r in candidates:
            feat=cache.get(r['symbol']) or {}
            if not feat or now-float(feat.get('_cached_at',0) or 0)>1800: continue
            row={**r,**{k:v for k,v in feat.items() if not k.startswith('_')}}
            nm=(row.get('name') or '').strip(); up=nm.upper(); reason=None
            if '스팩' in nm or 'SPAC' in up: reason='SPAC'
            elif ' ETN' in (' '+up) or up.endswith('ETN'): reason='ETN'
            elif nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or _re.search(r'\d+우B$',nm): reason='PREFERRED'
            if reason:
                row['exclude_reason']=reason; excluded.append(row); continue
            row['instrument_type']='ETF' if (up.startswith(etf_prefix) or ' ETF' in (' '+up)) else 'STOCK'
            best=min(int(row.get('value_rank',9999)),int(row.get('volume_rank',9999)))
            if row.get('macd_cross_5') and row.get('near_52w_high'):
                row['finder_tag']='PRIMARY_SIGNAL'; row['finder_score']=130.0
            elif row.get('macd_cross_5'):
                row['finder_tag']='MACD_FRESH'; row['finder_score']=110.0-best*0.7
            elif row.get('near_52w_high'):
                row['finder_tag']='LEADER_52W'; row['finder_score']=105.0-best*0.7
            else:
                row['finder_tag']='LIQUID_WATCH'; row['finder_score']=100.0-best*0.7
            rows.append(row)
        rows.sort(key=lambda r:(r.get('finder_score',0),-min(int(r.get('value_rank',9999)),int(r.get('volume_rank',9999)))),reverse=True)
        out=rows[:max(1,min(int(limit),100))]
        return {
            'ok':True,'finder_mode':'V53_NONBLOCKING_ETF_INCLUDED_INDEPENDENT_RANKS',
            'candidate_count':len(candidates),'evaluated_count':len(rows)+len(excluded),'finder_count':len(out),
            'stock_count':sum(1 for r in out if r.get('instrument_type')=='STOCK'),
            'etf_count':sum(1 for r in out if r.get('instrument_type')=='ETF'),
            'primary_signal_count':sum(1 for r in out if r.get('finder_tag')=='PRIMARY_SIGNAL'),
            'macd_fresh_count':sum(1 for r in out if r.get('macd_cross_5')),
            'near_52w_count':sum(1 for r in out if r.get('near_52w_high')),
            'cursor':getattr(self,'_v53_cursor',0),'rows':out,'excluded_rows':excluded[:30],
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
'''

API_PATCH=r'''

@app.get('/api/v5/korea-momentum-ranks-v53')
async def v53_korea_momentum_ranks():
    return await asyncio.to_thread(korea.momentum_rank_snapshot_v53)

@app.get('/api/v5/korea-momentum-finder-v53')
async def v53_korea_momentum_finder(batch_size:int=6,limit:int=40):
    return await asyncio.to_thread(korea.momentum_finder_v53,batch_size,limit)
'''

def main():
    s=KOREA.read_text()
    if 'def momentum_rank_snapshot_v53' not in s:
        anchor='    def discover(self, limit=50):\n'
        if anchor not in s: raise SystemExit('PATCH_TARGET_NOT_FOUND: Korea discover')
        s=s.replace(anchor,KOREA_PATCH+'\n'+anchor,1); KOREA.write_text(s)
    a=API.read_text()
    if '/api/v5/korea-momentum-ranks-v53' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+API_PATCH+'\n',1); API.write_text(a)
    print('FINDER_KOREA_NONBLOCKING_V53_OK')

if __name__=='__main__': main()
