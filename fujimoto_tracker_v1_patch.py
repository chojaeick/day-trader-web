from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== FUJIMOTO TRACKER V1 =====
_fujimoto_tracker_state={}
_fujimoto_tracker_cursor=0

@app.get('/api/v5/fujimoto-tracker/KOREA')
async def v5_fujimoto_tracker_korea(batch_size:int=5,limit:int=10,max_pages:int=1):
    def _run():
        import time as _time
        global _fujimoto_tracker_cursor

        snap=korea.momentum_rank_snapshot_v54()
        candidates=[]
        for r in list(snap.get('rows') or []):
            nm=str(r.get('name') or '').strip(); up=nm.upper()
            if '스팩' in nm or 'SPAC' in up: continue
            if ' ETN' in (' '+up) or up.endswith('ETN'): continue
            if nm.endswith('우') or nm.endswith('우B') or '우선주' in nm or re.search(r'\d+우B$',nm): continue
            lane=min(int(r.get('value_rank') or 9999),int(r.get('volume_rank') or 9999))
            row=dict(r)
            row['finder_rank_score']=max(0.0,100.0-min(lane,100)*0.6)
            cached=_fujimoto_overlay_cache.get(row.get('symbol')) or {}
            row['cached_fujimoto_score']=cached.get('score')
            row['cached_trade_priority']=(round(row['finder_rank_score']*0.40+float(cached.get('score'))*0.60,1)
                                          if cached.get('score') is not None else None)
            candidates.append(row)

        if not candidates:
            return {'ok':True,'version':'FUJIMOTO_TRACKER_V1','count':0,'rows':[]}

        # First preference: already scored high-priority names. Unscored names remain in rank order.
        candidates.sort(key=lambda x:(x.get('cached_trade_priority') is not None,
                                      x.get('cached_trade_priority') or -1,
                                      x.get('finder_rank_score') or 0),reverse=True)
        watch_pool=candidates[:max(10,min(int(limit)*2,30))]

        bs=max(1,min(int(batch_size),6,len(watch_pool)))
        start=int(_fujimoto_tracker_cursor)%len(watch_pool)
        batch=[watch_pool[(start+i)%len(watch_pool)] for i in range(bs)]
        _fujimoto_tracker_cursor=(start+bs)%len(watch_pool)
        now=_time.time()

        for r in batch:
            sym=r.get('symbol')
            prev=_fujimoto_tracker_state.get(sym) or {'state':'WATCH','position_open':False}
            try:
                d=korea.canonical_minute_bars(sym,max_pages=max(1,min(int(max_pages),2)))
                eng=evaluate_fujimoto_engine_v1(
                    d.get('bars') or [],
                    previous_state=prev.get('state') or 'WATCH',
                    position_open=bool(prev.get('position_open'))
                )
                # Tracker is signal-only. ENTRY does not flip position_open automatically.
                _fujimoto_tracker_state[sym]={
                    'state':eng.get('engine_state') or 'WATCH',
                    'position_open':bool(prev.get('position_open')),
                    'signal':eng.get('signal'),
                    'score':eng.get('score'),
                    'updated_at':datetime.now(timezone.utc).isoformat(),
                    'engine':eng,
                }
                # Reuse score in Finder overlay cache.
                sc=dict(eng); sc['_cached_at']=now; _fujimoto_overlay_cache[sym]=sc
            except Exception as e:
                _fujimoto_tracker_state[sym]={
                    'state':'DATA_INVALID','position_open':bool(prev.get('position_open')),
                    'signal':'NONE','score':None,'error':str(e)[:180],
                    'updated_at':datetime.now(timezone.utc).isoformat()
                }
            _time.sleep(0.20)

        rows=[]
        for r in watch_pool:
            sym=r.get('symbol')
            st=_fujimoto_tracker_state.get(sym) or {}
            eng=st.get('engine') or {}
            score=st.get('score')
            row=dict(r)
            row.update({
                'fujimoto_score':score,
                'engine_state':st.get('state') or 'NOT_EVALUATED',
                'signal':st.get('signal') or 'NONE',
                'position_open':bool(st.get('position_open')),
                'transition':eng.get('transition'),
                'actionable':bool(eng.get('actionable')),
                'entry_reasons':eng.get('entry_reasons') or [],
                'exit_reasons':eng.get('exit_reasons') or [],
                'rsi':eng.get('rsi'),'macd':eng.get('macd'),'macd_signal':eng.get('macd_signal'),'macd_hist':eng.get('macd_hist'),
                'latest_bar_time':eng.get('latest_bar_time'),
                'trade_priority':round(r['finder_rank_score']*0.40+float(score)*0.60,1) if score is not None else None,
            })
            rows.append(row)

        state_order={'ENTRY':0,'ENTRY_READY':1,'PREPARE':2,'HOLD':3,'PARTIAL_EXIT':4,'EXIT':5,'WATCH':6,'NOT_EVALUATED':7,'DATA_INVALID':8}
        rows.sort(key=lambda x:(x.get('trade_priority') is not None,
                                x.get('trade_priority') or -1,
                                -state_order.get(x.get('engine_state'),9)),reverse=True)
        rows=rows[:max(1,min(int(limit),20))]
        return {
            'ok':True,'version':'FUJIMOTO_TRACKER_V1','rank_status':snap.get('status'),
            'signal_only':True,'order_placement':False,
            'watch_pool_count':len(watch_pool),'evaluated_count':sum(1 for r in rows if r.get('fujimoto_score') is not None),
            'count':len(rows),'cursor':_fujimoto_tracker_cursor,'rows':rows,
            'updated_at':datetime.now(timezone.utc).isoformat(),
        }
    return await asyncio.to_thread(_run)

@app.post('/api/v5/fujimoto-tracker/KOREA/{symbol}/position')
async def v5_fujimoto_tracker_position(symbol:str,open:bool=False):
    cur=_fujimoto_tracker_state.get(symbol) or {'state':'WATCH','position_open':False}
    cur['position_open']=bool(open)
    if open and cur.get('state') in ('ENTRY','ENTRY_READY','PREPARE','WATCH'):
        cur['state']='HOLD'
    if not open and cur.get('state') in ('HOLD','PARTIAL_EXIT','EXIT'):
        cur['state']='WATCH'
    cur['updated_at']=datetime.now(timezone.utc).isoformat()
    _fujimoto_tracker_state[symbol]=cur
    return {'ok':True,'symbol':symbol,'state':cur.get('state'),'position_open':cur.get('position_open'),'signal_only':True}
'''

def main():
    a=API.read_text()
    if '/api/v5/fujimoto-tracker/KOREA' not in a:
        anchor="app=FastAPI(title='DAY TRADER LIVE API',version='3.5',lifespan=lifespan)\n"
        if anchor not in a: raise SystemExit('PATCH_TARGET_NOT_FOUND: FastAPI app')
        a=a.replace(anchor,anchor+PATCH+'\n',1)
        API.write_text(a)
    print('FUJIMOTO_TRACKER_V1_PATCH_OK')

if __name__=='__main__': main()
