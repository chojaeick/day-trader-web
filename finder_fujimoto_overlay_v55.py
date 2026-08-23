from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== V55 FINDER + FUJIMOTO OVERLAY =====
_fujimoto_overlay_cache={}
_fujimoto_overlay_cursor=0

@app.get('/api/v5/korea-finder-fujimoto-v55')
async def v55_korea_finder_fujimoto(batch_size:int=6,limit:int=40,max_pages:int=1):
    def _run():
        import time as _time
        global _fujimoto_overlay_cursor
        snap=korea.momentum_rank_snapshot_v54()
        candidates=list(snap.get('rows') or [])

        # Operational exclusions. ETFs remain eligible.
        cleaned=[]
        for r in candidates:
            nm=str(r.get('name') or '').strip(); up=nm.upper()
            reason=None
            if '스팩' in nm or 'SPAC' in up: reason='SPAC'
            elif ' ETN' in (' '+up) or up.endswith('ETN'): reason='ETN'
            elif nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or re.search(r'\d+우B$',nm): reason='PREFERRED'
            if reason: continue
            cleaned.append(dict(r))

        if not cleaned:
            return {'ok':True,'status':snap.get('status'),'candidate_count':0,'finder_count':0,'rows':[]}

        # Do not create a large synchronous request: rotate only a small batch.
        bs=max(1,min(int(batch_size),8,len(cleaned)))
        start=int(_fujimoto_overlay_cursor)%len(cleaned)
        batch=[cleaned[(start+i)%len(cleaned)] for i in range(bs)]
        _fujimoto_overlay_cursor=(start+bs)%len(cleaned)
        now=_time.time()

        for r in batch:
            sym=r.get('symbol')
            try:
                d=korea.canonical_minute_bars(sym,max_pages=max(1,min(int(max_pages),2)))
                score=evaluate_fujimoto_v1(d.get('bars') or [])
                score['_cached_at']=now
                _fujimoto_overlay_cache[sym]=score
            except Exception as e:
                _fujimoto_overlay_cache[sym]={'ok':False,'score':None,'state':'DATA_INVALID','reason':str(e)[:180],'_cached_at':now}
            _time.sleep(0.20)

        rows=[]
        for r in cleaned:
            sym=r.get('symbol')
            f=_fujimoto_overlay_cache.get(sym) or {}
            row=dict(r)
            row['fujimoto_score']=f.get('score')
            row['fujimoto_state']=f.get('state') or 'NOT_EVALUATED'
            row['fujimoto_actionable']=bool(f.get('actionable'))
            row['rsi']=f.get('rsi'); row['macd']=f.get('macd'); row['macd_signal']=f.get('macd_signal'); row['macd_hist']=f.get('macd_hist')
            row['ma20']=f.get('ma20'); row['latest_bar_time']=f.get('latest_bar_time')
            # Rank lane remains separate from Fujimoto.  Trade priority only combines after score exists.
            lane_rank=min(int(row.get('value_rank') or 9999),int(row.get('volume_rank') or 9999))
            row['finder_rank_score']=max(0.0,100.0-min(lane_rank,100)*0.6)
            if f.get('score') is not None:
                row['trade_priority']=round(row['finder_rank_score']*0.40+float(f.get('score'))*0.60,1)
            else:
                row['trade_priority']=None
            rows.append(row)

        rows.sort(key=lambda x:(x.get('trade_priority') is not None,x.get('trade_priority') or -1,x.get('finder_rank_score') or 0),reverse=True)
        rows=rows[:max(1,min(int(limit),100))]
        evaluated=sum(1 for r in rows if r.get('fujimoto_score') is not None)
        return {
            'ok':True,
            'version':'KOREA_FINDER_FUJIMOTO_V55',
            'rank_status':snap.get('status'),
            'rank_scope':snap.get('rank_scope'),
            'candidate_count':len(cleaned),
            'evaluated_count':evaluated,
            'finder_count':len(rows),
            'cursor':_fujimoto_overlay_cursor,
            'scoring':'trade_priority = finder_rank_score*0.40 + fujimoto_score*0.60',
            'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(_run)
'''

def main():
    a=API.read_text()
    if '/api/v5/korea-finder-fujimoto-v55' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+PATCH+'\n',1)
        API.write_text(a)
    print('FINDER_FUJIMOTO_OVERLAY_V55_OK')

if __name__=='__main__': main()
