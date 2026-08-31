from pathlib import Path
import re

ROOT=Path('/home/ubuntu/day-trader-api')
api_p=ROOT/'live_server/api.py'
app_p=ROOT/'app_v5.py'
us_p=ROOT/'live_server/kiwoom_us_mock_broker.py'
kr_p=ROOT/'live_server/engine5_v22_live_kr.py'
switch_p=ROOT/'live_server/trade_switch.py'

switch_code='''from __future__ import annotations\nimport json\nfrom pathlib import Path\nfrom datetime import datetime, timezone\n\n_SWITCH_FILE=Path('/home/ubuntu/day-trader-api/live_server/v5_market_auto_switch.json')\n_DEFAULT={'KOREA':False,'USA':False}\n\ndef _load():\n    try:\n        d=json.loads(_SWITCH_FILE.read_text())\n        return {k:bool(d.get(k,False)) for k in _DEFAULT}\n    except Exception:\n        return dict(_DEFAULT)\n\ndef _save(d):\n    _SWITCH_FILE.write_text(json.dumps(d,ensure_ascii=False,indent=2))\n\ndef is_market_auto_enabled(market:str)->bool:\n    m=str(market or '').upper()\n    return bool(_load().get(m,False))\n\ndef set_market_auto_enabled(market:str,enabled:bool):\n    m=str(market or '').upper()\n    if m not in _DEFAULT:\n        raise ValueError('market must be KOREA or USA')\n    d=_load(); d[m]=bool(enabled); _save(d)\n    return {'ok':True,'market':m,'enabled':bool(enabled),'updated_at':datetime.now(timezone.utc).isoformat()}\n\ndef snapshot():\n    d=_load()\n    return {'ok':True,'KOREA':d['KOREA'],'USA':d['USA'],'updated_at':datetime.now(timezone.utc).isoformat()}\n'''
switch_p.write_text(switch_code)

# API endpoints
s=api_p.read_text()
if 'V5_MARKET_AUTO_SWITCH_V1' not in s:
    anchor='from .premarket_briefing import build_premarket_briefing\n'
    if anchor not in s: raise SystemExit('API_IMPORT_ANCHOR_NOT_FOUND')
    s=s.replace(anchor,anchor+'from .trade_switch import is_market_auto_enabled, set_market_auto_enabled, snapshot as trade_switch_snapshot\n',1)
    block='''\n# ===== V5_MARKET_AUTO_SWITCH_V1 =====\n@app.get('/api/v5/auto-switch')\ndef v5_auto_switch_all():\n    return trade_switch_snapshot()\n\n@app.get('/api/v5/auto-switch/{market}')\ndef v5_auto_switch_get(market:str):\n    m=str(market or '').upper()\n    if m not in ('KOREA','USA'):\n        raise HTTPException(status_code=400,detail='market must be KOREA or USA')\n    return {'ok':True,'market':m,'enabled':is_market_auto_enabled(m)}\n\n@app.post('/api/v5/auto-switch/{market}/{action}')\ndef v5_auto_switch_set(market:str,action:str):\n    m=str(market or '').upper(); a=str(action or '').upper()\n    if m not in ('KOREA','USA'):\n        raise HTTPException(status_code=400,detail='market must be KOREA or USA')\n    if a not in ('ON','OFF'):\n        raise HTTPException(status_code=400,detail='action must be ON or OFF')\n    return set_market_auto_enabled(m,a=='ON')\n# ===== /V5_MARKET_AUTO_SWITCH_V1 =====\n\n'''
    # insert before first route decorator
    idx=s.find('@app.')
    if idx<0: raise SystemExit('API_ROUTE_ANCHOR_NOT_FOUND')
    s=s[:idx]+block+s[idx:]
    api_p.write_text(s)

# US broker hard kill gate
s=us_p.read_text()
if 'TRADE_SWITCH_US_GUARD_V1' not in s:
    anchor='import requests\n'
    if anchor not in s: raise SystemExit('US_IMPORT_ANCHOR_NOT_FOUND')
    s=s.replace(anchor,anchor+'from live_server.trade_switch import is_market_auto_enabled\n',1)
    old='''    def _ensure_order_enabled(self) -> None:\n        if not self.cfg.order_enable:\n            raise RuntimeError("US mock orders disabled. Set KIWOOM_MOCK_US_ORDER_ENABLE=1 for the round-trip test only.")\n'''
    new='''    def _ensure_order_enabled(self) -> None:\n        # TRADE_SWITCH_US_GUARD_V1: app OFF is an immediate broker-level kill switch.\n        if not is_market_auto_enabled('USA'):\n            raise RuntimeError("USA AUTO SWITCH OFF")\n        if not self.cfg.order_enable:\n            raise RuntimeError("US mock orders disabled. Set KIWOOM_MOCK_US_ORDER_ENABLE=1 for the round-trip test only.")\n'''
    if old not in s: raise SystemExit('US_ORDER_GUARD_ANCHOR_NOT_FOUND')
    s=s.replace(old,new,1); us_p.write_text(s)

# KR signal authority kill gate
s=kr_p.read_text()
if 'TRADE_SWITCH_KR_GUARD_V1' not in s:
    anchor='from live_server.engine5_v22_kr import early_entry_decision, normal_entry_decision\n'
    if anchor not in s: raise SystemExit('KR_IMPORT_ANCHOR_NOT_FOUND')
    s=s.replace(anchor,anchor+'from live_server.trade_switch import is_market_auto_enabled\n',1)
    old='''def evaluate_entry(row: dict) -> dict:\n    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)\n'''
    new='''def evaluate_entry(row: dict) -> dict:\n    sym = str((row or {}).get('symbol') or '').replace('A', '').zfill(6)\n    # TRADE_SWITCH_KR_GUARD_V1: emergency OFF suppresses every new KR entry signal.\n    if not is_market_auto_enabled('KOREA'):\n        return {'engine': ENGINE_NAME, 'symbol': sym, 'enter': False, 'reason': 'AUTO_SWITCH_OFF'}\n'''
    if old not in s: raise SystemExit('KR_ENTRY_ANCHOR_NOT_FOUND')
    s=s.replace(old,new,1); kr_p.write_text(s)

# Common app ON/OFF controls after selected market is resolved.
s=app_p.read_text()
if 'V5_MARKET_AUTO_SWITCH_UI_V1' not in s:
    marker="market=st.session_state['v5_market']\n"
    if marker not in s: raise SystemExit('APP_MARKET_ANCHOR_NOT_FOUND')
    ui='''market=st.session_state['v5_market']\n\n# ===== V5_MARKET_AUTO_SWITCH_UI_V1 =====\nauto_state=api(f'/api/v5/auto-switch/{market}',5)\nauto_on=bool(auto_state.get('enabled'))\nas1,as2,as3=st.columns([1.15,1.15,4.7])\nif as1.button('🟢 자동매매 ON',use_container_width=True,type='primary' if auto_on else 'secondary',key=f'auto_on_{market}'):\n    rr=post(f'/api/v5/auto-switch/{market}/ON',{},5)\n    if rr.get('ok'): st.rerun()\n    else: st.error(f'자동매매 ON 실패: {rr}')\nif as2.button('🔴 긴급 OFF',use_container_width=True,type='primary' if not auto_on else 'secondary',key=f'auto_off_{market}'):\n    rr=post(f'/api/v5/auto-switch/{market}/OFF',{},5)\n    if rr.get('ok'): st.rerun()\n    else: st.error(f'긴급 OFF 실패: {rr}')\nas3.markdown(('**AUTO ARMED · 신규 진입 허용**' if auto_on else '**AUTO OFF · 신규 진입 차단**') + '  \\n시세 연결은 유지됩니다. OFF는 주문/신규진입 차단용 긴급 스위치입니다.')\n# ===== /V5_MARKET_AUTO_SWITCH_UI_V1 =====\n'''
    s=s.replace(marker,ui,1); app_p.write_text(s)

print('V5_MARKET_AUTO_SWITCH_PATCHED')
