from pathlib import Path

API = Path('live_server/api.py')
APP = Path('app_v5.py')


def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'PATCH_TARGET_NOT_FOUND: {label}')
    return text.replace(old, new, 1)


def patch_api():
    s = API.read_text()

    anchor = "v4=CleanEngine(s.db_path)\n"
    inject = """v4=CleanEngine(s.db_path)\n\n# V5 runtime load mode. Connectivity/WebSocket stays alive in both modes;\n# only heavy Finder/Tracker analysis cadence changes.\nruntime_mode={\n    'mode':'NORMAL',\n    'updated_at':datetime.now(timezone.utc).isoformat(),\n}\n\ndef _runtime_profile():\n    daytrade=runtime_mode.get('mode')=='DAYTRADE'\n    return {\n        'mode':'DAYTRADE' if daytrade else 'NORMAL',\n        'tracker_seconds':5 if daytrade else 60,\n        'finder_seconds':30 if daytrade else 180,\n        'korea_tracker_seconds':10 if daytrade else 120,\n        'loop_seconds':2 if daytrade else 5,\n        'streaming':'ALWAYS_ON',\n    }\n"""
    s = replace_once(s, anchor, inject, 'runtime state')

    old = """async def v4_engine_forever():\n    last={'USA':0.0,'KOREA':0.0}\n    warmed_usa=set()\n"""
    new = """async def v4_engine_forever():\n    last={'USA':0.0,'KOREA':0.0}\n    last_tracker={'USA':0.0,'KOREA':0.0}\n    warmed_usa=set()\n"""
    s = replace_once(s, old, new, 'tracker timestamps')

    old = """            now=time.monotonic()\n            if now-last['USA']>=30:\n"""
    new = """            now=time.monotonic()\n            profile=_runtime_profile()\n            if now-last['USA']>=profile['finder_seconds']:\n"""
    s = replace_once(s, old, new, 'USA finder cadence')

    old = """            if now-last['KOREA']>=300:\n                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now\n\n            v4.refresh_usa_tracker(db)\n            v4.refresh_korea_tracker(korea)\n        except Exception:\n            logging.exception('V4 engine loop failed')\n        await asyncio.sleep(5)\n"""
    new = """            if now-last['KOREA']>=max(300,profile['finder_seconds']):\n                v4.build_korea_finder(korea.discovery,5); last['KOREA']=now\n\n            # Heavy analysis is cadence-controlled. Streaming and Kiwoom\n            # connectivity are NOT affected by runtime mode.\n            if now-last_tracker['USA']>=profile['tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_usa_tracker,db)\n                last_tracker['USA']=time.monotonic()\n\n            # Do not burn CPU on the closed Korean market in NORMAL mode.\n            kr_open=False\n            try:\n                kr_open=bool(korea._kst_market_open())\n            except Exception:\n                pass\n            if (profile['mode']=='DAYTRADE' or kr_open) and now-last_tracker['KOREA']>=profile['korea_tracker_seconds']:\n                await asyncio.to_thread(v4.refresh_korea_tracker,korea)\n                last_tracker['KOREA']=time.monotonic()\n        except Exception:\n            logging.exception('V4 engine loop failed')\n        await asyncio.sleep(_runtime_profile()['loop_seconds'])\n"""
    s = replace_once(s, old, new, 'tracker cadence block')

    endpoint_anchor = "app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])\n"
    endpoint = endpoint_anchor + """\n\n@app.get('/api/v4/runtime-mode')\nasync def get_runtime_mode():\n    return {'ok':True,**_runtime_profile(),'updated_at':runtime_mode.get('updated_at')}\n\n@app.post('/api/v4/runtime-mode/{mode}')\nasync def set_runtime_mode(mode:str):\n    m=str(mode or '').upper()\n    if m not in ('NORMAL','DAYTRADE'):\n        raise HTTPException(status_code=400,detail='mode must be NORMAL or DAYTRADE')\n    runtime_mode['mode']=m\n    runtime_mode['updated_at']=datetime.now(timezone.utc).isoformat()\n    logging.warning('V4 runtime mode changed to %s',m)\n    return {'ok':True,**_runtime_profile(),'updated_at':runtime_mode['updated_at']}\n"""
    s = replace_once(s, endpoint_anchor, endpoint, 'runtime endpoints')

    API.write_text(s)


def patch_app():
    s = APP.read_text()

    # Fix the known nested f-string syntax error if this branch still has it.
    bad = '''        with c4:st.markdown(f'<div class="hold-head">수익률</div><div class="hold-val">{f"{p[\\"pct\\"]:+.2f}%" if p["pct"] is not None else "-"}</div>',unsafe_allow_html=True)'''
    good = '''        pct_text = f'{p["pct"]:+.2f}%' if p["pct"] is not None else '-'\n        with c4:st.markdown(f'<div class="hold-head">수익률</div><div class="hold-val">{pct_text}</div>',unsafe_allow_html=True)'''
    if bad in s:
        s=s.replace(bad,good,1)

    # Make repeated registration widgets safe across Trading/Portfolio.
    s=s.replace("def render_manual_holding(market):", "def render_manual_holding(market,scope='trading'):", 1)
    for old,new in [
        ("key=f'msym_{market}'","key=f'msym_{market}_{scope}'"),
        ("key=f'mqty_{market}'","key=f'mqty_{market}_{scope}'"),
        ("key=f'mavg_{market}'","key=f'mavg_{market}_{scope}'"),
        ("key=f'mreg_{market}'","key=f'mreg_{market}_{scope}'"),
    ]:
        s=s.replace(old,new)
    s=s.replace("with add:render_manual_holding(market)","with add:render_manual_holding(market,'trading')")
    s=s.replace("    render_manual_holding(market)\n\ndef render_briefing", "    render_manual_holding(market,'portfolio')\n\ndef render_briefing")

    # Add a tiny helper for POST endpoints that don't need a JSON body.
    post_anchor = """def post(path,payload,timeout=10):\n    if not API_URL:return {'ok':False,'error':'DAYTRADER_API_URL is empty'}\n    try:\n        r=requests.post(API_URL+path,json=payload,timeout=timeout);r.raise_for_status();return r.json()\n    except Exception as e:return {'ok':False,'error':str(e)}\n"""
    if post_anchor in s and 'def runtime_mode_bar' not in s:
        helper = post_anchor + """\n\ndef runtime_mode_bar():\n    state=api('/api/v4/runtime-mode',5)\n    mode=str(state.get('mode') or 'NORMAL').upper()\n    c1,c2,c3,c4=st.columns([1.0,1.0,1.15,3.2])\n    c1.markdown('**⚙ 분석모드**')\n    if c2.button('NORMAL',use_container_width=True,type='primary' if mode=='NORMAL' else 'secondary',key='mode_normal'):\n        post('/api/v4/runtime-mode/NORMAL',{},5);st.rerun()\n    if c3.button('⚡ DAYTRADE',use_container_width=True,type='primary' if mode=='DAYTRADE' else 'secondary',key='mode_daytrade'):\n        post('/api/v4/runtime-mode/DAYTRADE',{},5);st.rerun()\n    c4.caption(f\"{mode} · Tracker {state.get('tracker_seconds','-')}s · Finder {state.get('finder_seconds','-')}s · Streaming ALWAYS ON\")\n"""
        s=s.replace(post_anchor,helper,1)

    market_anchor = "market=st.session_state['v5_market']\n"
    if market_anchor in s and 'runtime_mode_bar()' not in s.split(market_anchor,1)[1][:120]:
        s=s.replace(market_anchor,market_anchor+"runtime_mode_bar()\n",1)

    APP.write_text(s)


if __name__=='__main__':
    patch_api()
    patch_app()
    print('RUNTIME_MODE_PATCH_OK')
