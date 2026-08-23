from pathlib import Path

API=Path('live_server/api.py')

PATCH=r'''

# ===== FUJIMOTO POSITION SYNC V4 =====
def _fujimoto_daytrade_positions():
    out={}
    try:
        with sqlite3.connect(s.db_path,timeout=5) as c:
            c.row_factory=sqlite3.Row
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v5_portfolio_assets'").fetchone()
            if not exists:
                return out
            rows=c.execute("""
                SELECT market,symbol,name,bucket,quantity,avg_price
                FROM v5_portfolio_assets
                WHERE active=1 AND UPPER(market)='KOREA' AND quantity>0
            """).fetchall()
            for r in rows:
                d=dict(r); bucket=str(d.get('bucket') or '').upper()
                if bucket not in ('DAYTRADE','TRADING','SCALP','SHORT_TERM'):
                    continue
                sym=str(d.get('symbol') or '').upper().strip()
                if not sym: continue
                out[sym]=d
    except Exception as e:
        logging.warning('Fujimoto daytrade position sync failed: %s',e)
    return out

# Keep original v2 implementation and wrap its position state before each evaluation.
_fujimoto_tracker_v2_original=v5_fujimoto_tracker_v2_korea

@app.get('/api/v5/fujimoto-tracker-v4/KOREA')
async def v5_fujimoto_tracker_v4_korea(batch_size:int=2,limit:int=10,max_pages:int=1,cache_ttl_sec:int=180):
    held=_fujimoto_daytrade_positions()
    for sym,p in held.items():
        cur=_fujimoto_tracker_state.get(sym) or {'state':'HOLD','position_open':True}
        cur['position_open']=True
        if cur.get('state') in (None,'WATCH','PREPARE','ENTRY_READY','ENTRY','NOT_EVALUATED'):
            cur['state']='HOLD'
        cur['position_source']='V5_PORTFOLIO_DAYTRADE'
        cur['quantity']=float(p.get('quantity') or 0)
        cur['avg_price']=float(p.get('avg_price') or 0)
        cur['updated_at']=datetime.now(timezone.utc).isoformat()
        _fujimoto_tracker_state[sym]=cur

    result=await _fujimoto_tracker_v2_original(batch_size=batch_size,limit=limit,max_pages=max_pages,cache_ttl_sec=cache_ttl_sec)
    rows=list(result.get('rows') or [])
    by={str(r.get('symbol') or '').upper():r for r in rows}
    for sym,p in held.items():
        if sym in by:
            by[sym]['position_open']=True
            by[sym]['position_source']='V5_PORTFOLIO_DAYTRADE'
            by[sym]['quantity']=float(p.get('quantity') or 0)
            by[sym]['avg_price']=float(p.get('avg_price') or 0)
    result['version']='FUJIMOTO_TRACKER_V4_POSITION_SYNC'
    result['position_sync_source']='V5_PORTFOLIO_DAYTRADE'
    result['daytrade_position_count']=len(held)
    result['daytrade_positions']=list(held.values())
    return result

@app.get('/api/v5/fujimoto-positions/KOREA')
async def v5_fujimoto_positions_korea():
    held=_fujimoto_daytrade_positions()
    return {'ok':True,'source':'V5_PORTFOLIO_DAYTRADE','count':len(held),'rows':list(held.values())}
'''

AUTO_PATCH=r'''

# V4 position-aware auto loop; leaves v3 routes intact for rollback.
_fujimoto_auto_v4_status={'enabled':True,'running':False,'run_count':0,'last_error':None,'last_result':None,'last_started_at':None,'last_finished_at':None}

async def fujimoto_auto_v4_forever():
    await asyncio.sleep(20)
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)
            interval=10 if regular else 120
            if _fujimoto_auto_v4_status.get('enabled',True):
                _fujimoto_auto_v4_status['running']=True
                _fujimoto_auto_v4_status['last_started_at']=datetime.now(timezone.utc).isoformat()
                try:
                    r=await v5_fujimoto_tracker_v4_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))
                    _fujimoto_auto_v4_status['last_result']=r
                    _fujimoto_auto_v4_status['run_count']+=1
                    _fujimoto_auto_v4_status['last_error']=None
                except Exception as e:
                    _fujimoto_auto_v4_status['last_error']=str(e)[:300]
                    logging.exception('Fujimoto auto v4 failed')
                _fujimoto_auto_v4_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _fujimoto_auto_v4_status['running']=False
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _fujimoto_auto_v4_status['running']=False
            _fujimoto_auto_v4_status['last_error']=str(e)[:300]
            await asyncio.sleep(30)

@app.get('/api/v5/fujimoto-auto-v4/KOREA')
async def v5_fujimoto_auto_v4_status():
    r=_fujimoto_auto_v4_status.get('last_result') or {}
    return {
        'ok':True,'enabled':_fujimoto_auto_v4_status.get('enabled',True),'running':_fujimoto_auto_v4_status.get('running',False),
        'run_count':_fujimoto_auto_v4_status.get('run_count',0),'last_error':_fujimoto_auto_v4_status.get('last_error'),
        'last_started_at':_fujimoto_auto_v4_status.get('last_started_at'),'last_finished_at':_fujimoto_auto_v4_status.get('last_finished_at'),
        'rank_status':r.get('rank_status'),'watch_pool_count':r.get('watch_pool_count'),'evaluated_count':r.get('evaluated_count'),
        'cursor':r.get('cursor'),'fresh_fetch_count':r.get('fresh_fetch_count'),'cache_hit_count':r.get('cache_hit_count'),
        'daytrade_position_count':r.get('daytrade_position_count',0),'daytrade_positions':r.get('daytrade_positions') or [],
        'rows':r.get('rows') or [],'order_placement':False,'signal_only':True,
    }
'''

def main():
    a=API.read_text()
    if 'FUJIMOTO POSITION SYNC V4' not in a:
        a += PATCH + AUTO_PATCH
    if 'asyncio.create_task(fujimoto_auto_v4_forever())' not in a:
        target='tasks.append(asyncio.create_task(fujimoto_auto_forever()))'
        if target not in a:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: fujimoto auto task')
        a=a.replace(target,target+'\n    tasks.append(asyncio.create_task(fujimoto_auto_v4_forever()))',1)
    API.write_text(a)
    print('FUJIMOTO_POSITION_SYNC_V4_PATCH_OK')

if __name__=='__main__': main()
