from pathlib import Path
import re

API=Path('live_server/api.py')

BLOCK=r'''

# ===== DAYTRADE ENTRY AUTO V1.3 =====
_daytrade_entry_auto_status={
    'enabled':True,
    'running':False,
    'run_count':0,
    'last_started_at':None,
    'last_finished_at':None,
    'last_error':None,
    'last_result':None,
    'startup_delay_sec':20,
    'regular_interval_sec':30,
}

async def daytrade_entry_auto_forever():
    # Avoid competing with API/universe startup traffic.
    await asyncio.sleep(int(_daytrade_entry_auto_status.get('startup_delay_sec') or 20))
    while True:
        try:
            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
            mins=kst.hour*60+kst.minute
            regular=bool(kst.weekday()<5 and 540<=mins<930)

            # Outside KRX regular session do not hit Kiwoom ranking/chart APIs at all.
            if not regular:
                _daytrade_entry_auto_status['running']=False
                await asyncio.sleep(60)
                continue

            if not _daytrade_entry_auto_status.get('enabled',True):
                _daytrade_entry_auto_status['running']=False
                await asyncio.sleep(30)
                continue

            _daytrade_entry_auto_status['running']=True
            _daytrade_entry_auto_status['last_started_at']=datetime.now(timezone.utc).isoformat()
            try:
                result=await asyncio.to_thread(korea.daytrade_entry_v12,10,5,1)
                _daytrade_entry_auto_status['last_result']=result
                _daytrade_entry_auto_status['run_count']=int(_daytrade_entry_auto_status.get('run_count') or 0)+1
                _daytrade_entry_auto_status['last_error']=None
            except Exception as e:
                _daytrade_entry_auto_status['last_error']=str(e)[:300]
                logging.exception('Daytrade entry auto runner failed')
            finally:
                _daytrade_entry_auto_status['last_finished_at']=datetime.now(timezone.utc).isoformat()
                _daytrade_entry_auto_status['running']=False

            await asyncio.sleep(int(_daytrade_entry_auto_status.get('regular_interval_sec') or 30))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _daytrade_entry_auto_status['running']=False
            _daytrade_entry_auto_status['last_error']=str(e)[:300]
            logging.exception('Daytrade entry auto outer loop failed')
            await asyncio.sleep(30)

@app.get('/api/v5/daytrade-entry-auto/KOREA')
async def v5_daytrade_entry_auto_status():
    kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))
    mins=kst.hour*60+kst.minute
    regular=bool(kst.weekday()<5 and 540<=mins<930)
    r=_daytrade_entry_auto_status.get('last_result') or {}
    return {
        'ok':True,
        'version':'DAYTRADE_ENTRY_AUTO_V1_3',
        'enabled':bool(_daytrade_entry_auto_status.get('enabled',True)),
        'running':bool(_daytrade_entry_auto_status.get('running')),
        'run_count':int(_daytrade_entry_auto_status.get('run_count') or 0),
        'last_started_at':_daytrade_entry_auto_status.get('last_started_at'),
        'last_finished_at':_daytrade_entry_auto_status.get('last_finished_at'),
        'last_error':_daytrade_entry_auto_status.get('last_error'),
        'startup_delay_sec':int(_daytrade_entry_auto_status.get('startup_delay_sec') or 0),
        'regular_interval_sec':int(_daytrade_entry_auto_status.get('regular_interval_sec') or 30),
        'regular_open':regular,
        'kst_now':kst.isoformat(),
        'market_gate':r.get('market_gate'),
        'candidate_count':r.get('candidate_count'),
        'evaluated_count':r.get('evaluated_count'),
        'entry_candidate_count':r.get('entry_candidate_count') or 0,
        'ready_count':r.get('ready_count') or 0,
        'rows':r.get('rows') or [],
        'signal_only':True,
        'order_placement':False,
        'note':'Runner calls Kiwoom only during KRX regular session; UI should read this cache endpoint.',
    }

@app.post('/api/v5/daytrade-entry-auto/KOREA/toggle')
async def v5_daytrade_entry_auto_toggle(enabled:bool=True):
    _daytrade_entry_auto_status['enabled']=bool(enabled)
    return {'ok':True,'enabled':bool(enabled),'order_placement':False}
'''


def main():
    a=API.read_text()

    if 'DAYTRADE ENTRY AUTO V1.3' not in a:
        a += BLOCK

    # Inject into the existing FastAPI lifespan task list.
    if 'asyncio.create_task(daytrade_entry_auto_forever())' not in a:
        m=re.search(r'(@asynccontextmanager\s*\nasync def lifespan\(app:FastAPI\):)(.*?)(\n\s*yield\b)',a,re.S)
        if not m:
            m=re.search(r'(@asynccontextmanager\s*\nasync def lifespan\([^)]*\):)(.*?)(\n\s*yield\b)',a,re.S)
        if not m:
            raise SystemExit('PATCH_TARGET_NOT_FOUND: lifespan yield')
        body=m.group(2)
        inject='\n    tasks.append(asyncio.create_task(daytrade_entry_auto_forever()))\n'
        repl=m.group(1)+body+inject+m.group(3)
        a=a[:m.start()]+repl+a[m.end():]

    API.write_text(a)
    print('DAYTRADE_ENTRY_AUTO_V13_PATCH_OK')


if __name__=='__main__':
    main()
