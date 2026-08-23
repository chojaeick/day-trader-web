from pathlib import Path
import re

API=Path('live_server/api.py')

BLOCK=r'''

# ===== FUJIMOTO AUTO RUNNER V3 =====
_fujimoto_auto_status={
    'enabled':True,'running':False,'last_started_at':None,'last_finished_at':None,
    'last_error':None,'run_count':0,'last_result':None
}

async def fujimoto_auto_forever():
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)
            interval=10 if regular else 120
            if _fujimoto_auto_status.get('enabled',True):
                _fujimoto_auto_status['running']=True
                _fujimoto_auto_status['last_started_at']=datetime.now(timezone.utc).isoformat()
                try:
                    result=await v5_fujimoto_tracker_v2_korea(batch_size=2,limit=10,max_pages=1,cache_ttl_sec=(30 if regular else 180))
                    _fujimoto_auto_status['last_result']=result
                    _fujimoto_auto_status['run_count']=int(_fujimoto_auto_status.get('run_count') or 0)+1
                    _fujimoto_auto_status['last_error']=None
                except Exception as e:
                    _fujimoto_auto_status['last_error']=str(e)[:300]
                    logging.exception('Fujimoto auto runner failed')
                _fujimoto_auto_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _fujimoto_auto_status['running']=False
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _fujimoto_auto_status['running']=False
            _fujimoto_auto_status['last_error']=str(e)[:300]
            logging.exception('Fujimoto auto outer loop failed')
            await asyncio.sleep(30)

@app.get('/api/v5/fujimoto-auto/KOREA')
async def v5_fujimoto_auto_status():
    r=_fujimoto_auto_status.get('last_result') or {}
    return {
        'ok':True,
        'enabled':bool(_fujimoto_auto_status.get('enabled',True)),
        'running':bool(_fujimoto_auto_status.get('running')),
        'run_count':int(_fujimoto_auto_status.get('run_count') or 0),
        'last_started_at':_fujimoto_auto_status.get('last_started_at'),
        'last_finished_at':_fujimoto_auto_status.get('last_finished_at'),
        'last_error':_fujimoto_auto_status.get('last_error'),
        'rank_status':r.get('rank_status'),
        'watch_pool_count':r.get('watch_pool_count'),
        'evaluated_count':r.get('evaluated_count'),
        'cursor':r.get('cursor'),
        'fresh_fetch_count':r.get('fresh_fetch_count'),
        'cache_hit_count':r.get('cache_hit_count'),
        'rows':r.get('rows') or [],
        'order_placement':False,
        'signal_only':True,
    }

@app.post('/api/v5/fujimoto-auto/KOREA/toggle')
async def v5_fujimoto_auto_toggle(enabled:bool=True):
    _fujimoto_auto_status['enabled']=bool(enabled)
    return {'ok':True,'enabled':bool(enabled),'order_placement':False}
'''

def main():
    a=API.read_text()
    if 'FUJIMOTO AUTO RUNNER V3' not in a:
        a += BLOCK

    # Inject background task into existing lifespan just before yield.
    if "asyncio.create_task(fujimoto_auto_forever())" not in a:
        m=re.search(r'(@asynccontextmanager\s*\nasync def lifespan\(app:FastAPI\):)(.*?)(\n\s*yield\b)',a,re.S)
        if not m:
            m=re.search(r'(@asynccontextmanager\s*\nasync def lifespan\([^)]*\):)(.*?)(\n\s*yield\b)',a,re.S)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: lifespan yield')
        body=m.group(2)
        indent='    '
        inject="\n    tasks.append(asyncio.create_task(fujimoto_auto_forever()))\n"
        repl=m.group(1)+body+inject+m.group(3)
        a=a[:m.start()]+repl+a[m.end():]

    API.write_text(a)
    print('FUJIMOTO_AUTO_RUNNER_V3_PATCH_OK')

if __name__=='__main__':
    main()
