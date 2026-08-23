from pathlib import Path

API=Path('live_server/api.py')


def main():
    a=API.read_text()

    old="""async def fujimoto_auto_forever():\n    while True:\n        try:\n            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))\n"""
    new="""async def fujimoto_auto_forever():\n    # Let FastAPI finish startup and health/status endpoints become available\n    # before the first Kiwoom ranking/chart cycle begins.\n    await asyncio.sleep(15)\n    while True:\n        try:\n            kst=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Seoul'))\n"""
    if old in a:
        a=a.replace(old,new,1)
    elif 'async def fujimoto_auto_forever():' not in a:
        raise SystemExit('PATCH_TARGET_NOT_FOUND: fujimoto_auto_forever')

    # Make startup state explicit so the status endpoint is useful during the delay.
    old_status="""_fujimoto_auto_status={\n    'enabled':True,'running':False,'last_started_at':None,'last_finished_at':None,\n    'last_error':None,'run_count':0,'last_result':None\n}\n"""
    new_status="""_fujimoto_auto_status={\n    'enabled':True,'running':False,'last_started_at':None,'last_finished_at':None,\n    'last_error':None,'run_count':0,'last_result':None,\n    'startup_delay_sec':15\n}\n"""
    if old_status in a:
        a=a.replace(old_status,new_status,1)

    needle="""        'last_error':_fujimoto_auto_status.get('last_error'),\n        'rank_status':r.get('rank_status'),\n"""
    repl="""        'last_error':_fujimoto_auto_status.get('last_error'),\n        'startup_delay_sec':int(_fujimoto_auto_status.get('startup_delay_sec') or 0),\n        'rank_status':r.get('rank_status'),\n"""
    if needle in a and "'startup_delay_sec':int(_fujimoto_auto_status" not in a:
        a=a.replace(needle,repl,1)

    API.write_text(a)
    print('FUJIMOTO_AUTO_RUNNER_V3_STARTUP_FIX_OK')


if __name__=='__main__':
    main()
